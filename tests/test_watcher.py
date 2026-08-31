"""Offline checks for the logic that would fail silently if it broke.

No framework, no fixtures, no network: `python watch.py --selftest`.
Filters are pinned here so a local .env can never change what is asserted.
"""

import datetime as dt
import json
import os
import sys
import tempfile

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watcher import telegram
from watcher.movies import bms, state as state_mod
from watcher.movies.bms import shows_for, to_minutes
from watcher.config import IST
from watcher.movies.messages import shift_report, short_venue
from watcher.movies.shifts import shift_at
from watcher.telegram import poll_commands, wants_report


def demo():
    bms.VENUES, bms.FORMAT, bms.LANGUAGE = [], "4DX 3D", "English"   # never read .env
    assert to_minutes("07:10 PM") == 19 * 60 + 10
    assert to_minutes("08:00 AM") == 480
    assert to_minutes("23:45") == 23 * 60 + 45
    assert shows_for({"ShowDetails": [{"Date": "20260801", "Venues": []}]}, "20260808") == [], \
        "must not report today's shows for an unopened date"

    # Shape mirrors the live API: 4DX arrives as its own child event (as
    # ET00502630 does for this movie), and sometimes only as a venue Attribute.
    payload = {"ShowDetails": [{
        "Date": "20260808",
        "Event": {"ChildEvents": [
            {"EventCode": "E4DX", "EventLang": "English", "EventDimension": "4DX 3D"},
            {"EventCode": "E3D", "EventLang": "English", "EventDimension": "3D"},
            {"EventCode": "ETEL", "EventLang": "Telugu", "EventDimension": "4DX 3D"}]},
        "Venues": [
            {"VenueName": "PVR Nexus Mall", "ShowTimes": [
                {"ShowTime": "10:00 AM", "EventCode": "E4DX", "Attributes": "", "Availability": "A"},
                {"ShowTime": "11:30 PM", "EventCode": "E4DX", "Attributes": "", "Availability": "A"},
                {"ShowTime": "07:10 PM", "EventCode": "E4DX", "Attributes": "", "Availability": "S"},
                {"ShowTime": "02:00 PM", "EventCode": "E3D", "Attributes": "", "Availability": "A"},
                {"ShowTime": "03:00 PM", "EventCode": "ETEL", "Attributes": "", "Availability": "A"}]},
            {"VenueName": "AMB Cinemas", "ShowTimes": [
                {"ShowTime": "09:00 AM", "EventCode": "E3D", "Attributes": "ENGLISH 4DX", "Availability": "A"}]}]}]}
    got = [s for _, s in shows_for(payload, "20260808")]
    # 11:30 PM out of window; plain 3D, Telugu 4DX 3D and bare 4DX all rejected
    assert sorted((s["time"], s["sold"]) for s in got) == [
        ("07:10 PM", True), ("10:00 AM", False)], got
    assert all(s["format"] == "English 4DX 3D" for s in got), got

    # Same payload re-nested and renamed: the tolerant parse must still find it.
    drifted = {"data": {"page": {"cinemaList": [
        {"venueName": "PVR Nexus Mall", "sessions": [
            {"ShowTime": "10:00 AM", "EventCode": "E4DX", "Attributes": "",
             "Availability": "A", "ShowDateCode": "20260808"},
            {"ShowTime": "09:00 AM", "EventCode": "E4DX", "Attributes": "",
             "Availability": "A", "ShowDateCode": "20260807"}]}]}},
        "events": [{"EventCode": "E4DX", "EventLang": "English", "EventDimension": "4DX 3D"}]}
    got = [s for _, s in shows_for(drifted, "20260808")]
    assert [s["time"] for s in got] == ["10:00 AM"], got   # wrong day dropped

    # Drifted layout with no date evidence must stay silent, never guess.
    assert shows_for({"x": [{"venueName": "V", "sessions": [
        {"ShowTime": "10:00 AM", "EventCode": "E4DX"}]}],
        "events": [{"EventCode": "E4DX", "EventLang": "English",
                    "EventDimension": "4DX 3D"}]}, "20260808") == []

    # venue names must lose the screen-brand noise but keep the location
    assert short_venue("PVR Superplex Inorbit: LUXE, PXL, 4DX: Cyberabad") \
        == "PVR Superplex Inorbit, Cyberabad"
    assert short_venue("PVR: Irrum Manzil, Hyderabad") == "PVR Irrum Manzil"
    assert short_venue("AAA Cinemas: Ameerpet") == "AAA Cinemas, Ameerpet"

    # shift boundaries: every hour lands in exactly one shift, 00:00-07:00 in none
    at = lambda h: shift_at(dt.datetime(2026, 8, 1, h, 0, tzinfo=IST))
    assert [at(h) and at(h)[0] for h in (0, 6, 7, 11, 12, 17, 18, 20, 21, 23)] == [
        None, None, "Morning", "Morning", "Afternoon", "Afternoon",
        "Evening", "Evening", "Night", "Night"]
    assert len({at(h)[0] for h in range(7, 24)}) == 4      # all four reachable
    assert shift_report({"name": "Night", "date": "20260801", "checks": 18,
                         "first": "9:02 PM", "last": "11:54 PM", "errors": 0,
                         "found": {}, "bookable": "20260805"}).startswith("📋 NIGHT SHIFT")

    # telegram command parsing: only your chat, acked so it never replays
    real_post = requests.post
    telegram.requests.post = lambda *a, **k: type("R", (), {
        "status_code": 200,
        "json": staticmethod(lambda: {"result": [
            {"update_id": 7, "message": {"chat": {"id": 999}, "text": "/report"}},
            {"update_id": 8, "message": {"chat": {"id": 111}, "text": "/report@mybot"}},
            {"update_id": 9, "message": {"chat": {"id": 999}, "text": "hello"}}]})})()
    os.environ["TELEGRAM_API_TOKEN"] = os.environ.get("TELEGRAM_API_TOKEN") or "x"
    keep_chat = os.environ.get("TELEGRAM_CHAT_ID")
    os.environ["TELEGRAM_CHAT_ID"] = "999"
    st = {"tg_offset": 0}
    assert poll_commands(st) == [(999, "report"), (999, "hello")], "must ignore other chats"
    assert st["tg_offset"] == 10, st                 # acked past the last update
    telegram.requests.post = real_post
    if keep_chat is not None:
        os.environ["TELEGRAM_CHAT_ID"] = keep_chat

    # keyword matching: anywhere in the sentence, slash or not, any case
    for asked in ("report", "/report", "Status", "any update?", "hey whats the status",
                  "is it open yet", "check pls", "any news"):
        assert wants_report(asked.lower().lstrip("/")), asked
    for chat in ("hello", "hi", "thanks", "good morning"):
        assert not wants_report(chat), chat

    # legacy plain-list state must still load
    keep = state_mod.STATE_FILE
    state_mod.STATE_FILE = os.path.join(tempfile.gettempdir(), "_bms_legacy.json")
    with open(state_mod.STATE_FILE, "w") as f:
        json.dump(["a|b"], f)
    assert state_mod.load_state() == {"seen": ["a|b"], "shift": None, "tg_offset": 0}
    os.remove(state_mod.STATE_FILE)
    state_mod.STATE_FILE = keep

    demo_district()
    demo_brain()
    demo_movie_chat_scoping()
    demo_group_chats()
    demo_private_chats()

    # blank filters = report everything. Explicitly reset every field
    # shows_for() reads, not just FORMAT/LANGUAGE/VENUES - demo_brain()'s
    # multi-watch lifecycle test pushes real values onto bms's globals too
    # (watchspec._push() touches every _TARGETS module, bms included) and,
    # unlike the old single-watch design, nothing resets them back to .env
    # defaults afterward - there is no more "defaults" to fall back to.
    bms.FORMAT, bms.LANGUAGE, bms.VENUES = "", "", []
    bms.TIME_FROM, bms.TIME_TO = "06:00", "20:00"   # excludes the 11:30 PM show
    assert len(shows_for(payload, "20260808")) == 5
    print("self-check ok")


def demo_group_chats():
    """Owner-only group auto-adoption: the security check, persistence, and
    that a new group gets welcomed while an unauthorized add is ignored."""
    import tempfile
    from watcher import onboarding

    keep_file = telegram.KNOWN_CHATS_FILE
    telegram.KNOWN_CHATS_FILE = os.path.join(tempfile.gettempdir(), "_known_chats_test.json")
    if os.path.exists(telegram.KNOWN_CHATS_FILE):
        os.remove(telegram.KNOWN_CHATS_FILE)

    keep_chat = os.environ.get("TELEGRAM_CHAT_ID")
    keep_token = os.environ.get("TELEGRAM_API_TOKEN")
    os.environ["TELEGRAM_CHAT_ID"] = "111"          # the owner's own chat
    os.environ["TELEGRAM_API_TOKEN"] = "x"

    real_welcome = onboarding.welcome_message
    onboarding.welcome_message = lambda: "welcome text"   # no real LLM call

    sent_to = []
    pending = []
    real_post = telegram.requests.post

    def fake_post(url, json=None, **kw):
        if "sendMessage" in url:
            sent_to.append(json["chat_id"])
            return type("R", (), {"status_code": 200, "text": "",
                                  "json": staticmethod(lambda: {})})()
        return type("R", (), {"status_code": 200,
                    "json": staticmethod(lambda: {"result": pending})})()

    telegram.requests.post = fake_post

    def joined(chat_id, actor):
        return {"my_chat_member": {"chat": {"id": chat_id, "type": "group"},
                                   "from": {"id": actor},
                                   "new_chat_member": {"status": "member"}}}

    def left(chat_id, actor):
        return {"my_chat_member": {"chat": {"id": chat_id, "type": "group"},
                                   "from": {"id": actor},
                                   "new_chat_member": {"status": "kicked"}}}

    try:
        st = {"tg_offset": 0}

        # 1. the owner adds the bot to a new group -> adopted, welcomed there only
        pending = [dict(update_id=1, **joined(-500, 111))]
        poll_commands(st)
        assert "-500" in telegram._load_known_chats(), telegram._load_known_chats()
        assert sent_to == ["-500"], "must welcome only the new chat, not broadcast"

        # 2. someone ELSE adding the bot elsewhere must be ignored entirely -
        # this is the actual hijack-prevention check
        pending = [dict(update_id=2, **joined(-600, 999))]
        poll_commands(st)
        assert "-600" not in telegram._load_known_chats(), \
            "a non-owner group add must never be adopted"
        assert sent_to == ["-500"], "must not welcome an unauthorized chat"

        # 3. a message from the now-known group is accepted like any other chat
        pending = [{"update_id": 3, "message": {"chat": {"id": -500}, "text": "status"}}]
        assert poll_commands(st) == [(-500, "status")]

        # 4. losing access drops a chat regardless of who removed it - Telegram
        # itself reporting "kicked" is authoritative, no owner check needed here
        pending = [dict(update_id=4, **left(-500, 999))]
        poll_commands(st)
        assert "-500" not in telegram._load_known_chats()

        # 5. broadcast reaches every known chat, not just one
        pending = [dict(update_id=5, **joined(-700, 111))]
        poll_commands(st)
        sent_to.clear()
        assert telegram.send_telegram("hi")
        assert set(sent_to) == {"111", "-700"}, sent_to
    finally:
        telegram.requests.post = real_post
        onboarding.welcome_message = real_welcome
        if os.path.exists(telegram.KNOWN_CHATS_FILE):
            os.remove(telegram.KNOWN_CHATS_FILE)
        telegram.KNOWN_CHATS_FILE = keep_file
        if keep_chat is not None:
            os.environ["TELEGRAM_CHAT_ID"] = keep_chat
        else:
            os.environ.pop("TELEGRAM_CHAT_ID", None)
        if keep_token is not None:
            os.environ["TELEGRAM_API_TOKEN"] = keep_token


def demo_private_chats():
    """Individual DMs get dynamic responses without prior adoption (bug fix,
    16 Aug 2026 - a friend's DM got zero response while the owner's own DM
    and an adopted group both worked; poll_commands() was silently dropping
    any chat it didn't already know). Group adoption stays owner-only,
    unchanged - covered already by demo_group_chats(); this covers the new
    private-chat path plus that the two don't bleed into each other."""
    import tempfile
    from watcher import onboarding

    keep_known = telegram.KNOWN_CHATS_FILE
    keep_greeted = telegram.GREETED_PRIVATE_FILE
    telegram.KNOWN_CHATS_FILE = os.path.join(tempfile.gettempdir(), "_known_chats_test2.json")
    telegram.GREETED_PRIVATE_FILE = os.path.join(tempfile.gettempdir(), "_greeted_private_test.json")
    for f in (telegram.KNOWN_CHATS_FILE, telegram.GREETED_PRIVATE_FILE):
        if os.path.exists(f):
            os.remove(f)

    keep_chat = os.environ.get("TELEGRAM_CHAT_ID")
    keep_token = os.environ.get("TELEGRAM_API_TOKEN")
    os.environ["TELEGRAM_CHAT_ID"] = "111"          # the owner's own chat
    os.environ["TELEGRAM_API_TOKEN"] = "x"

    real_welcome = onboarding.welcome_message
    onboarding.welcome_message = lambda: "welcome text"

    sent_to = []
    pending = []
    real_post = telegram.requests.post

    def fake_post(url, json=None, **kw):
        if "sendMessage" in url:
            sent_to.append(json["chat_id"])
            return type("R", (), {"status_code": 200, "text": "",
                                  "json": staticmethod(lambda: {})})()
        return type("R", (), {"status_code": 200,
                    "json": staticmethod(lambda: {"result": pending})})()

    telegram.requests.post = fake_post

    def dm(chat_id, text, update_id):
        return {"update_id": update_id, "message": {
            "chat": {"id": chat_id, "type": "private"}, "text": text}}

    try:
        st = {"tg_offset": 0}

        # 1. the owner's own DM still works exactly as before (chat 111,
        # already "known" via TELEGRAM_CHAT_ID - not the new code path at all)
        pending = [dm(111, "status", 1)]
        assert poll_commands(st) == [(111, "status")]
        assert sent_to == [], "the owner is already known - no welcome expected"

        # 2. a brand-new stranger's DM is accepted (the actual bug fix) and
        # gets welcomed on this, its first-ever message
        pending = [dm(222, "hello", 2)]
        assert poll_commands(st) == [(222, "hello")], \
            "a private chat's message must never be silently dropped"
        assert sent_to == ["222"], "first contact from a new private chat must be welcomed"

        # 3. that same stranger's SECOND message must not re-welcome them
        sent_to.clear()
        pending = [dm(222, "status", 3)]
        assert poll_commands(st) == [(222, "status")]
        assert sent_to == [], "must not welcome the same private chat twice"

        # 4. a private chat is NEVER added to the broadcast list, no matter
        # how many messages it sends - shift reports must not leak to it
        assert "222" not in telegram._load_known_chats(), \
            "a private DM chat must never become a broadcast target"
        sent_to.clear()
        telegram.send_telegram("shift report text")
        assert "222" not in sent_to, "broadcast must not reach a private-chat stranger"

        # 5. group adoption is still owner-only - a non-owner adding the bot
        # to a group must still be ignored (regression check, not the new path)
        sent_to.clear()
        pending = [{"update_id": 6, "my_chat_member": {
            "chat": {"id": -900, "type": "group"}, "from": {"id": 999},
            "new_chat_member": {"status": "member"}}}]
        poll_commands(st)
        assert "-900" not in telegram._load_known_chats(), \
            "a non-owner group add must still be refused after this change"
        assert sent_to == [], "must not welcome an unauthorized group"
    finally:
        telegram.requests.post = real_post
        onboarding.welcome_message = real_welcome
        for f in (telegram.KNOWN_CHATS_FILE, telegram.GREETED_PRIVATE_FILE):
            if os.path.exists(f):
                os.remove(f)
        telegram.KNOWN_CHATS_FILE = keep_known
        telegram.GREETED_PRIVATE_FILE = keep_greeted
        if keep_chat is not None:
            os.environ["TELEGRAM_CHAT_ID"] = keep_chat
        else:
            os.environ.pop("TELEGRAM_CHAT_ID", None)
        if keep_token is not None:
            os.environ["TELEGRAM_API_TOKEN"] = keep_token


def demo_district():
    """District parsing: UTC->IST, the format filter, venue filter, date guard."""
    from watcher.movies import district
    district.FORMAT, district.VENUES = "4DX 3D", ["irrum manzil"]
    district.TIME_FROM, district.TIME_TO = "06:00", "20:00"

    def page(search_date, sessions):
        return {"props": {"pageProps": {"data": {"serverState": {
            "movieSessions": {"grp" + search_date: {
                "searchDate": search_date,
                "arrangedSessions": [
                    {"entityName": "PVR Irrum Manzil, Khairatabad, Hyderabad",
                     "sessions": sessions},
                    {"entityName": "AMB Cinemas, Gachibowli",
                     "sessions": [{"showTime": "2026-08-08T05:00", "scrnFmt": "4DX-3D",
                                   "avail": 50, "audi": "A1"}]}]},
            },
            "mdpV2MovieData": {"194537": {"showDates": ["2026-08-08", "2026-08-09"]}},
        }}}}}

    got = district.shows_for(page("2026-08-08", [
        # 04:40 UTC == 10:10 IST, inside the window
        {"showTime": "2026-08-08T04:40", "scrnFmt": "4DX-3D", "avail": 2, "audi": "AUDI 01 4DX"},
        # 17:15 UTC == 22:45 IST, outside the window
        {"showTime": "2026-08-08T17:15", "scrnFmt": "4DX-3D", "avail": 9, "audi": "AUDI 01 4DX"},
        # right format-ish but not 4DX 3D
        {"showTime": "2026-08-08T05:00", "scrnFmt": "3D", "avail": 9, "audi": "AUDI 02"},
        {"showTime": "2026-08-08T05:30", "scrnFmt": "2D", "avail": 9, "audi": "AUDI 03"},
        # sold out but still worth reporting
        {"showTime": "2026-08-08T11:15", "scrnFmt": "4DX-3D", "avail": 0, "audi": "AUDI 01 4DX"},
    ]), "20260808")
    times = sorted((s["time"], s["sold"]) for _, s in got)
    assert times == [("10:10 AM", False), ("4:45 PM", True)], times
    assert all("Irrum Manzil" in s["venue"] for _, s in got), got   # venue filter held
    assert all(s["format"] == "4DX-3D" for _, s in got), got

    # District echoing a different date must never be reported as ours
    assert district.shows_for(page("2026-08-05", [
        {"showTime": "2026-08-05T04:40", "scrnFmt": "4DX-3D", "avail": 5, "audi": "A"}]),
        "20260808") == []

    assert district.show_dates(page("2026-08-08", [])) == ["2026-08-08", "2026-08-09"]
    assert district.to_iso("20260808") == "2026-08-08"
    # a page with no __NEXT_DATA__ shape at all must be empty, not an exception
    assert district.shows_for({}, "20260808") == []

    # seat-category price/filtering - real field shapes from a live-captured
    # session object (sid/pid/cid/mid/areas), 15 Aug 2026
    real_areas = [
        {"code": "CR", "label": "CLASSIC ROWS", "price": 140, "sAvail": 82},
        {"code": "QR", "label": "PRIME ROWS", "price": 185, "sAvail": 71},
        {"code": "BR", "label": "RECLINER ROWS", "price": 285, "sAvail": 5},
    ]
    sold_out_recliner = [
        {"code": "CR", "label": "CLASSIC ROWS", "price": 140, "sAvail": 40},
        {"code": "BR", "label": "RECLINER ROWS", "price": 285, "sAvail": 0},
    ]
    no_prime_at_all = [{"code": "CR", "label": "CLASSIC ROWS", "price": 140, "sAvail": 20}]

    assert district._cheapest_area(real_areas, "") == real_areas[0], \
        "no filter -> cheapest available area overall (CLASSIC ROWS, 140)"
    assert district._cheapest_area(real_areas, "prime")["price"] == 185
    assert district._cheapest_area(real_areas, "recliner")["price"] == 285
    assert district._cheapest_area(sold_out_recliner, "recliner") is None, \
        "a matching category with zero seats available must not count as a match"
    assert district._cheapest_area(no_prime_at_all, "prime") is None, \
        "no area matches the wanted category at all"

    try:
        # no filter: price now comes through for real (used to always be "")
        district.SEAT_CATEGORY = ""
        got = district.shows_for(page("2026-08-08", [
            {"showTime": "2026-08-08T04:40", "scrnFmt": "4DX-3D", "avail": 2,
             "audi": "AUDI 01 4DX", "areas": real_areas},
        ]), "20260808")
        assert len(got) == 1 and got[0][1]["price"] == 140, got

        # filtered: only the session with a real, available PRIME ROWS match
        # survives; the price/seat_category shown are for THAT category, not
        # the cheapest overall
        district.SEAT_CATEGORY = "prime"
        got = district.shows_for(page("2026-08-08", [
            {"showTime": "2026-08-08T04:40", "scrnFmt": "4DX-3D", "avail": 2,
             "audi": "AUDI 01 4DX", "areas": real_areas},
            {"showTime": "2026-08-08T05:30", "scrnFmt": "4DX-3D", "avail": 9,
             "audi": "AUDI 02 4DX", "areas": no_prime_at_all},
        ]), "20260808")
        assert len(got) == 1, \
            "the session with no PRIME ROWS at all must be filtered out: %r" % (got,)
        assert got[0][1]["price"] == 185 and got[0][1]["seat_category"] == "PRIME ROWS", got
    finally:
        district.SEAT_CATEGORY = ""


if __name__ == "__main__":
    demo()


def demo_brain():
    """Routing and the watch lifecycle - all offline, no LLM, no network."""
    import tempfile
    from watcher import llm
    from watcher.movies import brain, search, watchspec

    chat_id = 999

    # keyword commands must work with the LLM completely unavailable
    real_available, llm.available = llm.available, lambda: False
    reply = brain.handle("tell me a joke", chat_id, {}, [], None)
    assert "keywords" in reply.lower(), reply
    assert "report" in reply.lower(), reply
    # ...and a status request must never reach the LLM at all - either a real
    # shift report (📋, an active watch) or the idle message (😴, none right
    # now) is a valid deterministic answer; what must never happen is falling
    # through to the model, which llm.available()==False would catch anyway.
    reply = brain.handle("status", chat_id, {"20260808": []}, [], "20260813")
    assert reply.startswith("📋") or reply.startswith("😴"), \
        "status must be answered from data, not the model: %r" % reply

    # a title that does not exist is refused, never turned into a dead URL
    real_find, search.find = search.find, lambda *a, **k: None
    assert "couldn't find" in brain._apply_new_watch(
        {"title": "a film that does not exist", "dates": ["2026-08-08"]}, chat_id).lower()

    # live events are declined honestly rather than half-supported
    assert search.looks_like_event("Coldplay concert")
    assert search.looks_like_event("India vs Australia cricket match")
    assert not search.looks_like_event("Spider-Man: Brand New Day")
    assert "live event" in brain._apply_new_watch(
        {"title": "Coldplay concert", "dates": ["2026-08-08"]}, chat_id).lower()

    # found, but no dates given -> ask, do not guess
    search.find = lambda *a, **k: {"title": "Some Film", "url": "https://x/y",
                                   "city": "hyderabad", "movie_id": "1", "cities": []}
    assert "which dates" in brain._apply_new_watch({"title": "Some Film"}, chat_id).lower()

    # full lifecycle: start a watch, apply it, finish it - and a second,
    # independent watch alongside it (multi-watch, not just one-at-a-time)
    keep_watch, keep_state = watchspec.WATCH_FILE, None
    watchspec.WATCH_FILE = os.path.join(tempfile.gettempdir(), "_watch_test.json")
    from watcher.movies import state as sm
    keep_state, sm.STATE_FILE = sm.STATE_FILE, os.path.join(tempfile.gettempdir(),
                                                            "_state_test.json")
    try:
        msg = brain._apply_new_watch({
            "title": "Some Film", "dates": ["2026-08-08"], "format": "IMAX",
            "venues": ["Forum"], "time_from": "10:00", "time_to": "22:00"}, chat_id)
        assert "Watching" in msg, msg
        active = watchspec.load_all()
        assert len(active) == 1 and active[0]["active"] and active[0]["format"] == "IMAX", active
        spec1 = active[0]

        # apply() pushes exactly this one spec's fields onto the live globals -
        # run_cycle() calls it once per active spec, right before scanning it
        from watcher.movies import district
        watchspec.apply(spec1)
        assert district.FORMAT == "IMAX" and district.VENUES == ["forum"], district.FORMAT
        assert district.DATES == ["20260808"], district.DATES

        # a second, independent movie can be watched at the same time
        msg2 = brain._apply_new_watch({
            "title": "Some Film", "dates": ["2026-08-09"], "format": "2D",
            "venues": [], "time_from": "00:00", "time_to": "23:59"}, chat_id)
        assert "Watching" in msg2, msg2
        assert len(watchspec.load_all()) == 2, watchspec.load_all()

        # asking to watch the SAME movie+dates again reinforces spec1 rather
        # than creating a wasteful third, duplicate entry
        brain._apply_new_watch({
            "title": "Some Film", "dates": ["2026-08-08"], "format": "IMAX",
            "venues": ["Forum"], "time_from": "10:00", "time_to": "22:00"}, chat_id)
        assert len(watchspec.load_all()) == 2, "must dedup against an identical watch"

        # simulate a restart: globals get wiped, re-applying the reloaded spec
        # must put the exact same values back - boot() itself no longer pushes
        # anything (see its docstring), run_cycle() re-applies fresh every cycle
        district.FORMAT, district.VENUES, district.DATES = "WIPED", [], ["19990101"]
        reloaded = next(w for w in watchspec.load_all() if w["id"] == spec1["id"])
        watchspec.apply(reloaded)
        assert district.FORMAT == "IMAX", "apply() must restore the stored spec's values"
        assert district.DATES == ["20260808"], district.DATES

        ended = watchspec.finish(spec1["id"], "found")
        assert len(ended) == 1 and ended[0]["active"] is False and ended[0]["ended_reason"] == "found"
        remaining = watchspec.load_all()
        assert len(remaining) == 1 and remaining[0]["format"] == "2D", \
            "finishing one watch must not touch the other"

        ended_all = watchspec.finish(None, "cancelled")
        assert len(ended_all) == 1
        assert watchspec.load_all() == [], "finish(None) must stop every active watch"
    finally:
        for f in (watchspec.WATCH_FILE, sm.STATE_FILE):
            if os.path.exists(f):
                os.remove(f)
        watchspec.WATCH_FILE, sm.STATE_FILE = keep_watch, keep_state
        search.find, llm.available = real_find, real_available


def demo_movie_chat_scoping():
    """A movie watch belongs to the chat that created it - same reported bug
    and same fix as watcher/bus: (1) an alert must reach only the owning
    chat, (2) status/facts must only see this chat's own watches, (3) cancel
    must never touch another chat's watch."""
    import tempfile
    from watcher.movies import brain, runner, watchspec

    OWNER, GROUP = 100, -500
    keep_watch = watchspec.WATCH_FILE
    watchspec.WATCH_FILE = os.path.join(tempfile.gettempdir(), "_movie_chat_scope_test.json")
    sent = []
    real_reply_to, runner.reply_to = runner.reply_to, lambda c, t: sent.append((c, t))
    real_send, runner.send_telegram = runner.send_telegram, lambda t: sent.append((None, t))

    try:
        owner_watch = watchspec.start({"title": "Owner Movie", "dates": ["2026-08-20"],
                                       "chat_id": OWNER})
        group_watch = watchspec.start({"title": "Group Movie", "dates": ["2026-08-21"],
                                       "chat_id": GROUP})

        # (2) status: each chat sees only its own watch
        assert [w["id"] for w in watchspec.load_all(OWNER)] == [owner_watch["id"]]
        assert [w["id"] for w in watchspec.load_all(GROUP)] == [group_watch["id"]]

        # (1) alert: _send_to_owning_chat() must route to the watch's own chat,
        # never broadcast - this was the actual reported leak
        runner._send_to_owning_chat(owner_watch, "owner alert text")
        runner._send_to_owning_chat(group_watch, "group alert text")
        assert (OWNER, "owner alert text") in sent, sent
        assert (GROUP, "group alert text") in sent, sent
        assert not any(c is None for c, _ in sent), \
            "a chat-tagged watch's alert must never broadcast: %r" % sent

        # a legacy watch (no chat_id at all) must still fall back to broadcast,
        # so an already-running pre-migration watch doesn't go silent
        sent.clear()
        runner._send_to_owning_chat({"title": "Legacy"}, "legacy alert text")
        assert sent == [(None, "legacy alert text")], sent

        # (3) cancel: a bare "cancel" from the owner must not touch the group's watch
        reply = brain.handle("cancel", OWNER, {}, [], None)
        assert "Stopped watching" in reply, reply
        assert watchspec.load_all(OWNER) == [], "owner's own watch must be gone"
        assert [w["id"] for w in watchspec.load_all(GROUP)] == [group_watch["id"]], \
            "the group's watch must survive the owner cancelling their own"
    finally:
        if os.path.exists(watchspec.WATCH_FILE):
            os.remove(watchspec.WATCH_FILE)
        watchspec.WATCH_FILE = keep_watch
        runner.reply_to = real_reply_to
        runner.send_telegram = real_send
