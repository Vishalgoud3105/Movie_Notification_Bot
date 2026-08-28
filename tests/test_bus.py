"""Offline checks for the bus domain and the movie/bus router.

No framework, no fixtures, no network: run via `python watch.py --selftest`
(which calls this after tests/test_watcher.py's demo()). send_telegram is
monkey-patched everywhere a cycle might fire one, so this never touches the
real bot regardless of what's in .env.
"""

import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watcher.bus import runner as bus_runner
from watcher.bus import watchspec
from watcher.telegram import wants_report


def _future(days_ahead=30):
    """A travel date safely in the future, for any test that actually drives
    run_cycle_bus() (which auto-expires a watch whose date has passed) - a
    hardcoded "future" date rots as real time passes it. Bit us once: a
    fixture pinned to "20260820" quietly became a past date and every such
    test started silently expiring its own watch instead of scanning it."""
    return (dt.datetime.now() + dt.timedelta(days=days_ahead)).strftime("%Y%m%d")


def _future_iso(days_ahead=30):
    """Same as _future() but "YYYY-MM-DD", the shape brain._apply_new_watch()
    takes directly (bypassing the LLM's extraction)."""
    return (dt.datetime.now() + dt.timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def demo():
    keep_watch = watchspec.WATCH_FILE
    watchspec.WATCH_FILE = os.path.join(tempfile.gettempdir(), "_bus_watch_test.json")
    sent = []
    real_send, bus_runner.send_telegram = bus_runner.send_telegram, sent.append

    try:
        _demo_watchspec_lifecycle()
        _demo_multi_watch()
        _demo_run_cycle(sent)
        _demo_stale_status()
        _demo_shift_report()
        _demo_router()
        _demo_refuse_unresolvable()
        _demo_seat_filtering()
        _demo_quality_and_govt_filter()
        _demo_chat_scoping()
    finally:
        if os.path.exists(watchspec.WATCH_FILE):
            os.remove(watchspec.WATCH_FILE)
        watchspec.WATCH_FILE = keep_watch
        bus_runner.send_telegram = real_send

    print("bus self-check ok")


def _demo_watchspec_lifecycle():
    spec = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                            "date": "20260820", "target_price": None})
    assert spec["active"] and spec["lowest_seen"] is None, spec
    wid = spec["id"]

    active = watchspec.load()
    assert active and active["from_city"] == "hyderabad", active

    updated = watchspec.note_price(wid, 750)
    assert updated["lowest_seen"] == 750, updated
    assert watchspec.load()["lowest_seen"] == 750

    ended = watchspec.finish(wid, "cancelled")
    assert len(ended) == 1 and ended[0]["active"] is False and ended[0]["ended_reason"] == "cancelled", ended
    assert watchspec.load() is None, "a finished watch must not stay active"

    # a "modify" (start() called again with the SAME id) must not lose the
    # progress already made - only a genuinely different route resets it
    s1 = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                          "date": "20260820", "target_price": None})
    watchspec.note_price(s1["id"], 700)
    modified = watchspec.start({"id": s1["id"], "from_city": "hyderabad",
                                "to_city": "bangalore", "date": "20260820",
                                "target_price": 650})
    assert modified["lowest_seen"] == 700, \
        "modifying target_price must not wipe the lowest price already found"
    assert modified["id"] == s1["id"], "a modify must reuse the same id"

    fresh = watchspec.start({"from_city": "chennai", "to_city": "pune",
                             "date": "20260901", "target_price": None})
    assert fresh["lowest_seen"] is None, "a genuinely new route must start fresh"
    assert fresh["id"] != modified["id"], "a different route must get its own id"

    watchspec.finish(None, "cancelled")   # clean up everything for the next test
    assert watchspec.load_all() == []


def _demo_multi_watch():
    """Several routes can be active - and watched, and cancelled - at once."""
    r1 = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                          "date": "20260820", "target_price": None})
    r2 = watchspec.start({"from_city": "chennai", "to_city": "pune",
                          "date": "20260825", "target_price": None})
    assert len(watchspec.load_all()) == 2, watchspec.load_all()

    # find() matches a named route among several active ones
    match = watchspec.find(None, to_city="bangalore")
    assert match and match["id"] == r1["id"], match

    # finishing one by id must not touch the other
    watchspec.finish(r1["id"], "cancelled")
    remaining = watchspec.load_all()
    assert len(remaining) == 1 and remaining[0]["id"] == r2["id"], remaining

    watchspec.finish(None, "cancelled")
    assert watchspec.load_all() == []


def _demo_run_cycle(sent):
    """run_cycle_bus() drives sources.scan() - fake it, no real endpoint exists yet."""
    from watcher.bus import sources

    state = {"shift": None}
    travel_date = _future()
    spec = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                            "date": travel_date, "target_price": None})
    wid = spec["id"]

    fake_hits = [
        {"operator": "Orange Travels", "price": 900, "seat_type": "sleeper",
         "depart": "22:00", "arrive": "06:00", "seats_left": 5, "source": "abhibus"},
        {"operator": "VRL", "price": 850, "seat_type": "seater",
         "depart": "21:30", "arrive": "05:30", "seats_left": 2, "source": "abhibus"},
    ]
    real_scan, sources.scan = sources.scan, lambda *a, **k: (fake_hits, [])

    try:
        # first cycle: no prior low, the cheapest (850) is a new low -> one alert
        bus_runner.run_cycle_bus(state)
        assert len(sent) == 1 and "850" in sent[0], sent
        assert watchspec.load()["lowest_seen"] == 850

        # same fares again: nothing cheaper -> no new alert
        bus_runner.run_cycle_bus(state)
        assert len(sent) == 1, "must not re-alert an unchanged price"

        # a cheaper fare appears -> alerts again
        sources.scan = lambda *a, **k: ([{"operator": "SRS", "price": 700,
                                          "seat_type": "seater", "source": "abhibus"}], [])
        bus_runner.run_cycle_bus(state)
        assert len(sent) == 2 and "700" in sent[1], sent
        assert watchspec.load()["lowest_seen"] == 700

        # a target price is added (same id - a modify, not a new watch) -> met
        # immediately -> one alert, then the watch ends
        watchspec.start({"id": wid, "from_city": "hyderabad", "to_city": "bangalore",
                         "date": travel_date, "target_price": 650})
        sources.scan = lambda *a, **k: ([{"operator": "SRS", "price": 600,
                                          "seat_type": "seater", "source": "abhibus"}], [])
        bus_runner.run_cycle_bus(state)
        assert len(sent) == 3 and "TARGET" in sent[2], sent
        assert watchspec.load() is None, "target reached must end the watch"

        # a travel date already in the past must auto-expire, no alert, no scan
        watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                         "date": "19990101", "target_price": None})
        scanned = []
        sources.scan = lambda *a, **k: (scanned.append(1) or ([], []))
        bus_runner.run_cycle_bus(state)
        assert not scanned, "an expired watch must not scan at all"
        assert watchspec.load() is None, "an expired watch must be finished"
        assert len(sent) == 3, "an expiry must not alert"
    finally:
        sources.scan = real_scan


def _demo_stale_status():
    """status must never show fares left over from a route that was
    cancelled/changed since the last scan - reported live: watch the 16th,
    cancel it, watch the 14th instead, then ask "status" before the next
    scheduled scan runs - it showed the 16th's old fare as if it were the
    14th's. run_cycle_bus() tags each hit with its watch id; live_report()
    must group hits by that tag rather than pooling everything together.
    """
    from watcher.bus import sources
    from watcher.bus.messages import live_report

    state = {"shift": None}
    real_scan = sources.scan

    try:
        route16 = watchspec.start({"from_city": "hyderabad", "to_city": "nellore",
                                   "date": _future(16), "target_price": None})
        sources.scan = lambda *a, **k: ([{"operator": "Old Route Bus", "price": 999,
                                          "seat_type": "seater", "source": "abhibus",
                                          "rating": 4.5, "no_of_ratings": 30}], [])
        hits, broken = bus_runner.run_cycle_bus(state)
        assert any(h.get("_watch_id") == route16["id"] for h in hits), hits

        # cancel the 16th, start the 14th instead - no scan has happened for
        # it yet, `hits` here is still what run_cycle_bus() just returned
        # for the 16th (exactly the gap between scheduled scans in --serve)
        watchspec.finish(route16["id"], "cancelled")
        watchspec.start({"from_city": "hyderabad", "to_city": "nellore",
                         "date": _future(14), "target_price": None})

        reply = live_report(watchspec.load_all(), hits, broken)
        assert "999" not in reply, \
            "must not show the cancelled route's stale fare: %r" % reply
        assert "Old Route Bus" not in reply, reply
        assert "no fares found yet" in reply.lower(), reply
    finally:
        sources.scan = real_scan
        watchspec.finish(None, "cancelled")


def _demo_shift_report():
    """Bus shift reports mirror the movie ones' shape - fixed data, no wall clock.

    shift_at()'s boundary math itself is already pinned in test_watcher.py; this
    only checks the bus-specific formatting and the "only send when a shift
    actually ended" transition, same as movies/shifts.py is never exercised
    through the live clock in the movie tests either.
    """
    from watcher.bus.messages import shift_report
    from watcher.bus.shifts import maybe_report_shift

    watching = shift_report({"name": "Night", "date": "20260801", "checks": 12,
                             "first": "9:02 PM", "last": "11:54 PM", "errors": 0,
                             "routes": {"abc123": {
                                 "route": "Hyderabad -> Bangalore · on Thursday, 20 Aug",
                                 "lowest": 700}}})
    assert watching.startswith("🚌 NIGHT SHIFT"), watching
    assert "700" in watching and "Bangalore" in watching, watching

    # several routes active during the same shift must each get their own
    # line, not just whichever was processed last (the old single-route bug)
    multi = shift_report({"name": "Night", "date": "20260801", "checks": 20,
                          "first": "9:00 PM", "last": "11:50 PM", "errors": 0,
                          "routes": {
                              "a": {"route": "Hyderabad -> Bangalore · on Thu, 20 Aug", "lowest": 700},
                              "b": {"route": "Hyderabad -> Nellore · on Fri, 21 Aug", "lowest": None},
                          }})
    assert "Bangalore" in multi and "Nellore" in multi, multi
    assert "700" in multi and "No fares found yet" in multi, multi

    idle = shift_report({"name": "Morning", "date": "20260801", "checks": 0,
                         "first": None, "last": None, "errors": 0, "routes": {}})
    assert "No bus route being watched" in idle, idle

    # a tally for a shift that has ended (name/date mismatch) must be sent once
    # and replaced, exactly like the movie side's maybe_report_shift()
    sent = []
    import watcher.bus.shifts as bus_shifts
    real_send, bus_shifts.send_telegram = bus_shifts.send_telegram, sent.append
    try:
        # bug fix: a watch that ended MID-shift (watching=False now) must still
        # get its stale tally flushed once that shift's boundary is crossed -
        # not silently dropped just because nothing is being watched anymore
        state = {"shift": {"name": "Night", "date": "19990101", "checks": 3,
                           "first": "1:00 AM", "last": "1:30 AM", "errors": 0,
                           "routes": {}}}
        maybe_report_shift(state, watching=False)
        assert len(sent) == 1 and sent[0].startswith("🚌 NIGHT SHIFT"), sent
        assert state["shift"] is None, "a flushed tally must be cleared"

        # and once flushed, watching=False must NOT auto-open a fresh idle one
        # (that would just spam an empty report at the next boundary forever)
        maybe_report_shift(state, watching=False)
        assert state["shift"] is None, "must not open a tally with nothing watched"

        # bug fix: a route cancelled mid-shift must not still appear in the
        # report sent at the end of that shift - reported live: cancel a bus
        # watch, and the next shift report kept listing it anyway.
        sent.clear()
        r1 = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                              "date": "20260820", "target_price": None})
        r2 = watchspec.start({"from_city": "chennai", "to_city": "pune",
                              "date": "20260821", "target_price": None})
        state = {"shift": {"name": "Night", "date": "19990101", "checks": 5,
                           "first": "9:00 PM", "last": "9:30 PM", "errors": 0,
                           "routes": {
                               r1["id"]: {"route": "Hyderabad -> Bangalore", "lowest": 700},
                               r2["id"]: {"route": "Chennai -> Pune", "lowest": 500},
                           }}}
        watchspec.finish(r1["id"], "cancelled")
        maybe_report_shift(state, watching=True)
        assert len(sent) == 1, sent
        assert "Bangalore" not in sent[0], \
            "a cancelled route must not appear in the shift report: %r" % sent[0]
        assert "Pune" in sent[0], "the still-active route must still be reported"
    finally:
        bus_shifts.send_telegram = real_send
        watchspec.finish(None, "cancelled")


def _demo_router():
    from watcher import router
    from watcher.movies import watchspec as movie_watchspec

    assert router.classify("watch bus from Hyderabad to Bangalore on 20 Aug") == ["bus"]
    assert router.classify("any abhibus fares yet?") == ["bus"]
    assert router.classify("watch Spider-Man 4DX 3D at Irrum Manzil") == ["movie"]
    assert router.classify("what's showing at the cinema tonight") == ["movie"]

    # a message naming BOTH domains must dispatch to both - not let one
    # keyword match eat the entire message and silently drop the other half
    both = router.classify("watch spiderman 4dx 3d and also watch bus from hyd to blr")
    assert set(both) == {"movie", "bus"}, both

    # deterministic domain-qualified commands: "check bus"/"cancel movie" must
    # route to exactly that domain, no LLM guessing needed
    assert router.classify("check bus") == ["bus"]
    assert router.classify("cancel movie") == ["movie"]
    assert router.classify("bus status") == ["bus"]

    # ambiguous wording routes to whichever domain actually has a live watch
    keep_bus, watchspec.WATCH_FILE = watchspec.WATCH_FILE, os.path.join(
        tempfile.gettempdir(), "_bus_router_test.json")
    keep_movie, movie_watchspec.WATCH_FILE = movie_watchspec.WATCH_FILE, os.path.join(
        tempfile.gettempdir(), "_movie_router_test.json")
    try:
        watchspec.start({"from_city": "a", "to_city": "b", "date": "20260820",
                         "target_price": None})
        assert router.classify("status") == ["bus"]
        assert router.classify("cancel") == ["bus"]
        watchspec.finish(None, "cancelled")

        # bug fix: a chat-tagged watch owned by chat 999 must not make an
        # UNRELATED chat's bare "status" route to bus - ambiguous routing is
        # chat-scoped the same way the watch data itself is. Neither domain
        # active for chat 42 -> falls through to the Mistral-unavailable default.
        from watcher import llm
        real_available, llm.available = llm.available, lambda: False
        watchspec.start({"from_city": "c", "to_city": "d", "date": "20260820",
                         "target_price": None, "chat_id": 999})
        assert router.classify("status", chat_id=42) == ["movie"], \
            "chat 42 has no bus watch of its own and must not be routed to " \
            "bus just because chat 999 has one active"
        llm.available = real_available
        watchspec.finish(None, "cancelled")

        # neither active, Mistral unreachable in this offline test -> falls back to movie
        real_available, llm.available = llm.available, lambda: False
        assert router.classify("status") == ["movie"]
        llm.available = real_available
    finally:
        for f in (watchspec.WATCH_FILE, movie_watchspec.WATCH_FILE):
            if os.path.exists(f):
                os.remove(f)
        watchspec.WATCH_FILE, movie_watchspec.WATCH_FILE = keep_bus, keep_movie

    assert wants_report("status")   # shared keyword list, sanity check it still loads


def _demo_refuse_unresolvable():
    """brain._apply_new_watch() must refuse a route upfront when AbhiBus can't
    serve it, instead of starting a watch that can never succeed - and must
    NOT refuse just because the check itself failed to run (a network hiccup
    is not the same as "this city doesn't exist")."""
    from watcher.bus import abhibus, brain

    real_resolve = abhibus.resolve
    chat_id = 111
    travel_date = _future_iso()
    try:
        abhibus.resolve = lambda name: (None, "no-direct-hub")
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "amalapuram",
                                      "date": travel_date}, chat_id)
        assert "doesn't sell direct tickets" in msg, msg
        assert watchspec.load() is None, "a refused route must never start a watch"

        abhibus.resolve = lambda name: (None, "not-found")
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "zzzznotreal",
                                      "date": travel_date}, chat_id)
        assert "couldn't find" in msg.lower(), msg
        assert watchspec.load() is None

        # a transient failure (id=None, note=None) must NOT block starting the watch
        abhibus.resolve = lambda name: (None, None)
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": travel_date}, chat_id)
        assert "Watching" in msg, msg
        assert watchspec.load() is not None, \
            "a transient validation failure must not block starting the watch"
        watchspec.finish(None, "cancelled")

        # a real match proceeds normally
        abhibus.resolve = lambda name: (7, None)
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": travel_date}, chat_id)
        assert "Watching" in msg, msg
        watchspec.finish(None, "cancelled")

        # same city twice, a date already in the past, and a target_price the
        # LLM hallucinated as text must all be refused before ever touching
        # AbhiBus or starting a dead/broken watch
        msg = brain._apply_new_watch({"from_city": "Hyderabad", "to_city": "hyderabad",
                                      "date": travel_date}, chat_id)
        assert "same city" in msg.lower(), msg
        assert watchspec.load() is None

        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": "2020-01-01"}, chat_id)
        assert "already passed" in msg.lower(), msg
        assert watchspec.load() is None

        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": travel_date, "target_price": "cheap please"}, chat_id)
        assert "didn't understand" in msg.lower(), msg
        assert watchspec.load() is None
    finally:
        abhibus.resolve = real_resolve


def _demo_chat_scoping():
    """A watch belongs to the chat that created it - reported live: watching
    a bus route from the owner's DM alerted into a group too, and the
    group's own watch showed up in the owner's "status"/cancel. Covers the
    three symptoms reported: (1) alerts must reach only the owning chat,
    (2) status must only show this chat's own routes, (3) cancel must never
    touch another chat's watch.
    """
    from watcher.bus import brain, sources

    OWNER, GROUP = 100, -500
    real_scan = sources.scan
    sent_scoped = []      # (chat_id, text) via reply_to
    real_reply_to, bus_runner.reply_to = bus_runner.reply_to, lambda c, t: sent_scoped.append((c, t))

    try:
        owner_watch = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                                       "date": _future(20), "target_price": None,
                                       "chat_id": OWNER})
        group_watch = watchspec.start({"from_city": "chennai", "to_city": "pune",
                                       "date": _future(21), "target_price": None,
                                       "chat_id": GROUP})

        # (2) status: each chat sees only its own route
        assert [w["id"] for w in watchspec.load_all(OWNER)] == [owner_watch["id"]]
        assert [w["id"] for w in watchspec.load_all(GROUP)] == [group_watch["id"]]

        # (1) alert: a new low on the owner's route must reach only OWNER
        sources.scan = lambda from_city, to_city, date_code, **k: (
            [{"operator": "SRS Travels", "price": 900, "source": "abhibus",
              "rating": 4.5, "no_of_ratings": 40}]
            if from_city == "hyderabad" else
            [{"operator": "VRL Travels", "price": 500, "source": "abhibus",
              "rating": 4.5, "no_of_ratings": 40}], [])
        state = {"shift": None}
        bus_runner.run_cycle_bus(state)
        chats_alerted = {c for c, _ in sent_scoped}
        assert chats_alerted == {OWNER, GROUP}, \
            "each route's own new-low alert must reach only its owning chat: %r" % sent_scoped
        owner_texts = [t for c, t in sent_scoped if c == OWNER]
        group_texts = [t for c, t in sent_scoped if c == GROUP]
        assert any("Bangalore" in t for t in owner_texts) and not any("Pune" in t for t in owner_texts), \
            "owner's chat must not see the group's route in its own alert"
        assert any("Pune" in t for t in group_texts) and not any("Bangalore" in t for t in group_texts), \
            "group's chat must not see the owner's route in its own alert"

        # (3) cancel: a bare "cancel" typed by the owner must not touch the group's watch
        reply = brain.handle("cancel", OWNER)
        assert "Stopped watching" in reply, reply
        assert watchspec.load_all(OWNER) == [], "owner's own watch must be gone"
        assert [w["id"] for w in watchspec.load_all(GROUP)] == [group_watch["id"]], \
            "the group's watch must survive the owner cancelling their own"
    finally:
        sources.scan = real_scan
        bus_runner.reply_to = real_reply_to
        watchspec.finish(None, "cancelled")


def _demo_quality_and_govt_filter():
    """Government-operator exclusion and the rating quality bar - the two
    filters added 15 Aug 2026. Regex verified live against a real 100-operator
    Hyderabad->Bangalore result set (TGSRTC, KSRTC Karnataka caught, all 98
    private names clean) before this test was written; this locks that in."""
    from watcher.bus import abhibus

    for govt in ("TGSRTC", "KSRTC Karnataka", "APSRTC", "MSRTC", "TNSTC",
                "Tamil Nadu State Road Transport Corporation"):
        assert abhibus._is_government_operator(govt), govt

    for private in ("SBM Transport", "Delta Transport Pvt Ltd", "Highline Transports",
                    "Ira Transport", "VRL Travels", "SRS Travels", "zingbus plus",
                    "Sri Vengamamba Bus Transport (SVBT)"):
        assert not abhibus._is_government_operator(private), \
            "%r wrongly caught by the govt-operator pattern" % private

    good = {"rating": 4.2, "no_of_ratings": 50}
    low_rating = {"rating": 3.0, "no_of_ratings": 50}
    thin_ratings = {"rating": 4.9, "no_of_ratings": 2}
    unrated = {"rating": None, "no_of_ratings": None}
    assert abhibus._meets_quality_bar(good)
    assert not abhibus._meets_quality_bar(low_rating)
    assert not abhibus._meets_quality_bar(thin_ratings)
    assert not abhibus._meets_quality_bar(unrated), \
        "an unrated bus must not pass by default - no signal isn't a safety signal"
    # search() wiring itself (govt-exclusion + bar applied to real service
    # dicts, before MAX_SEAT_CHECKS) was verified live against real AbhiBus
    # data, not re-mocked here - see the session's live-check output: 0
    # government operators and 0 below-bar buses leaked through a real
    # 254-service Hyderabad->Bangalore result.


def _demo_seat_filtering():
    """AC/seat-type/gender filtering - the pure parsing/matching functions,
    no network. Field mapping cross-checked 14 Aug 2026 against a real
    captured response's "gentsSeats" list (LD5 there had "M" at this exact
    position - see abhibus.py's module docstring)."""
    from watcher.bus import abhibus

    assert abhibus._matches_ac("AC Sleeper (2+1)", "ac") is True
    assert abhibus._matches_ac("AC Sleeper (2+1)", "non_ac") is False
    assert abhibus._matches_ac("NON-AC Seater (2+2)", "non_ac") is True
    assert abhibus._matches_ac("NON-AC Seater (2+2)", "ac") is False, \
        "'ac' must not match inside 'non-ac' - substring trap"
    assert abhibus._matches_ac("anything", None) is True, "no filter = matches everything"

    # real seat strings, lower deck, from an actual captured GetSeatLayout response
    raw = {"TotalSeatList": {"lowerdeck_seat_nos": [
        "LD1, 1, 1, LB, Y, M, 1534, 0, 76.55, 0.00, 0, 0,h,1342",   # available, male, berth
        "LD3, 4, 1, LB, N, F, 1074, 0, 53.59, 0.00, 0, 0,h,939",    # NOT available
        "LD5, 2, 3, LB, Y, M, 1200, 0, 60.00, 0.00, 0, 0,h,1051",   # available, male, cheaper
    ], "upperdeck_seat_nos": [
        "UD1, 1, 1, S, Y, F, 900, 0, 45.00, 0.00, 0, 0,h,800",      # available, female, seater
    ]}}
    seats = abhibus._parse_seats(raw)
    assert len(seats) == 4, seats
    ld1 = next(s for s in seats if s["seat_no"] == "LD1")
    assert ld1 == {"seat_no": "LD1", "seat_type": "sleeper", "available": True,
                   "gender": "male", "fare": 1534.0}, ld1
    ud1 = next(s for s in seats if s["seat_no"] == "UD1")
    assert ud1["seat_type"] == "seater" and ud1["gender"] == "female", ud1

    # cheapest matching seat: must skip the unavailable one (LD3) and the
    # wrong-gender one (UD1), landing on LD5 (cheaper than LD1, both male)
    best = abhibus._cheapest_matching_seat(seats, gender="male", seat_type="sleeper")
    assert best["seat_no"] == "LD5" and best["fare"] == 1200.0, best

    best_female = abhibus._cheapest_matching_seat(seats, gender="female", seat_type=None)
    assert best_female["seat_no"] == "UD1", best_female     # LD3 excluded: not available

    assert abhibus._cheapest_matching_seat(seats, gender="female", seat_type="sleeper") is None, \
        "no available female sleeper seat exists in this fixture"

    assert abhibus._cheapest_matching_seat(seats, gender=None, seat_type=None)["seat_no"] == "UD1", \
        "no filter at all -> cheapest available seat overall (UD1 @900, cheaper than LD5 @1200)"


if __name__ == "__main__":
    demo()
