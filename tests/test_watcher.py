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

from watcher import bms, state as state_mod, telegram
from watcher.bms import shows_for, to_minutes
from watcher.config import IST
from watcher.messages import shift_report, short_venue
from watcher.shifts import shift_at
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
    assert poll_commands(st) == ["report", "hello"], "must ignore other chats"
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

    # blank filters = report everything. VENUES too: demo_brain() restores the
    # .env defaults on exit, which re-applies a venue filter to every module.
    bms.FORMAT, bms.LANGUAGE, bms.VENUES = "", "", []
    assert len(shows_for(payload, "20260808")) == 5
    print("self-check ok")


def demo_district():
    """District parsing: UTC->IST, the format filter, venue filter, date guard."""
    from watcher import district
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


if __name__ == "__main__":
    demo()


def demo_brain():
    """Routing and the watch lifecycle - all offline, no Groq, no network."""
    import tempfile
    from watcher import brain, llm, search, watchspec

    # keyword commands must work with the LLM completely unavailable
    real_available, llm.available = llm.available, lambda: False
    reply = brain.handle("tell me a joke", {}, [], None)
    assert "keywords" in reply.lower(), reply
    assert "report" in reply.lower(), reply
    # ...and a status request must never reach the LLM at all
    assert brain.handle("status", {"20260808": []}, [], "20260813").startswith("📋"), \
        "status must be answered from data, not the model"

    # a title that does not exist is refused, never turned into a dead URL
    real_find, search.find = search.find, lambda *a, **k: None
    assert "couldn't find" in brain._apply_new_watch(
        {"title": "a film that does not exist", "dates": ["2026-08-08"]}).lower()

    # live events are declined honestly rather than half-supported
    assert search.looks_like_event("Coldplay concert")
    assert search.looks_like_event("India vs Australia cricket match")
    assert not search.looks_like_event("Spider-Man: Brand New Day")
    assert "live event" in brain._apply_new_watch(
        {"title": "Coldplay concert", "dates": ["2026-08-08"]}).lower()

    # found, but no dates given -> ask, do not guess
    search.find = lambda *a, **k: {"title": "Some Film", "url": "https://x/y",
                                   "city": "hyderabad", "movie_id": "1", "cities": []}
    assert "which dates" in brain._apply_new_watch({"title": "Some Film"}).lower()

    # full lifecycle: start a watch, then finish it and fall back to defaults
    keep_watch, keep_state = watchspec.WATCH_FILE, None
    watchspec.WATCH_FILE = os.path.join(tempfile.gettempdir(), "_watch_test.json")
    from watcher import state as sm
    keep_state, sm.STATE_FILE = sm.STATE_FILE, os.path.join(tempfile.gettempdir(),
                                                            "_state_test.json")
    try:
        msg = brain._apply_new_watch({
            "title": "Some Film", "dates": ["2026-08-08"], "format": "IMAX",
            "venues": ["Forum"], "time_from": "10:00", "time_to": "22:00"})
        assert "Watching" in msg, msg
        active = watchspec.load()
        assert active and active["active"] and active["format"] == "IMAX", active
        # the live values must actually have moved, not just been stored
        from watcher import district
        assert district.FORMAT == "IMAX" and district.VENUES == ["forum"], district.FORMAT
        assert district.DATES == ["20260808"], district.DATES

        # simulate a restart: the process forgets everything, boot() must put
        # the stored watch back or the watcher silently reverts to .env
        district.FORMAT, district.VENUES, district.DATES = "WIPED", [], ["19990101"]
        from watcher.runner import boot
        resumed = boot()
        assert resumed and resumed["format"] == "IMAX", resumed
        assert district.FORMAT == "IMAX", "boot() must re-apply the stored watch"
        assert district.DATES == ["20260808"], district.DATES

        ended = watchspec.finish("found")
        assert ended["active"] is False and ended["ended_reason"] == "found"
        assert watchspec.load() is None, "a finished watch must not stay active"
        assert district.FORMAT == watchspec._DEFAULTS["format"], "must restore defaults"
    finally:
        for f in (watchspec.WATCH_FILE, sm.STATE_FILE):
            if os.path.exists(f):
                os.remove(f)
        watchspec.WATCH_FILE, sm.STATE_FILE = keep_watch, keep_state
        search.find, llm.available = real_find, real_available
        watchspec.apply(None)
