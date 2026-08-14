"""Offline checks for the bus domain and the movie/bus router.

No framework, no fixtures, no network: run via `python watch.py --selftest`
(which calls this after tests/test_watcher.py's demo()). send_telegram is
monkey-patched everywhere a cycle might fire one, so this never touches the
real bot regardless of what's in .env.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watcher.bus import runner as bus_runner
from watcher.bus import watchspec
from watcher.telegram import wants_report


def demo():
    keep_watch = watchspec.WATCH_FILE
    watchspec.WATCH_FILE = os.path.join(tempfile.gettempdir(), "_bus_watch_test.json")
    sent = []
    real_send, bus_runner.send_telegram = bus_runner.send_telegram, sent.append

    try:
        _demo_watchspec_lifecycle()
        _demo_multi_watch()
        _demo_run_cycle(sent)
        _demo_shift_report()
        _demo_router()
        _demo_refuse_unresolvable()
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
    match = watchspec.find(to_city="bangalore")
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
    spec = watchspec.start({"from_city": "hyderabad", "to_city": "bangalore",
                            "date": "20260820", "target_price": None})
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
                         "date": "20260820", "target_price": 650})
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
                             "route": "Hyderabad -> Bangalore · on Thursday, 20 Aug",
                             "lowest": 700})
    assert watching.startswith("🚌 NIGHT SHIFT"), watching
    assert "700" in watching and "Bangalore" in watching, watching

    idle = shift_report({"name": "Morning", "date": "20260801", "checks": 0,
                         "first": None, "last": None, "errors": 0,
                         "route": None, "lowest": None})
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
                           "route": None, "lowest": None}}
        maybe_report_shift(state, watching=False)
        assert len(sent) == 1 and sent[0].startswith("🚌 NIGHT SHIFT"), sent
        assert state["shift"] is None, "a flushed tally must be cleared"

        # and once flushed, watching=False must NOT auto-open a fresh idle one
        # (that would just spam an empty report at the next boundary forever)
        maybe_report_shift(state, watching=False)
        assert state["shift"] is None, "must not open a tally with nothing watched"
    finally:
        bus_shifts.send_telegram = real_send


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

        # neither active, Groq unreachable in this offline test -> falls back to movie
        from watcher import llm
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
    try:
        abhibus.resolve = lambda name: (None, "no-direct-hub")
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "amalapuram",
                                      "date": "2026-08-20"})
        assert "doesn't sell direct tickets" in msg, msg
        assert watchspec.load() is None, "a refused route must never start a watch"

        abhibus.resolve = lambda name: (None, "not-found")
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "zzzznotreal",
                                      "date": "2026-08-20"})
        assert "couldn't find" in msg.lower(), msg
        assert watchspec.load() is None

        # a transient failure (id=None, note=None) must NOT block starting the watch
        abhibus.resolve = lambda name: (None, None)
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": "2026-08-20"})
        assert "Watching" in msg, msg
        assert watchspec.load() is not None, \
            "a transient validation failure must not block starting the watch"
        watchspec.finish(None, "cancelled")

        # a real match proceeds normally
        abhibus.resolve = lambda name: (7, None)
        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": "2026-08-20"})
        assert "Watching" in msg, msg
        watchspec.finish(None, "cancelled")

        # same city twice, a date already in the past, and a target_price the
        # LLM hallucinated as text must all be refused before ever touching
        # AbhiBus or starting a dead/broken watch
        msg = brain._apply_new_watch({"from_city": "Hyderabad", "to_city": "hyderabad",
                                      "date": "2026-08-20"})
        assert "same city" in msg.lower(), msg
        assert watchspec.load() is None

        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": "2020-01-01"})
        assert "already passed" in msg.lower(), msg
        assert watchspec.load() is None

        msg = brain._apply_new_watch({"from_city": "hyderabad", "to_city": "bangalore",
                                      "date": "2026-08-20", "target_price": "cheap please"})
        assert "didn't understand" in msg.lower(), msg
        assert watchspec.load() is None
    finally:
        abhibus.resolve = real_resolve


if __name__ == "__main__":
    demo()
