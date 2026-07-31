"""Offline checks for the logic that would fail silently if it broke.

No framework, no fixtures, no network: `python bms_watch.py --selftest`.
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

    bms.FORMAT, bms.LANGUAGE = "", ""       # blank filters = report everything
    assert len(shows_for(payload, "20260808")) == 5
    print("self-check ok")


if __name__ == "__main__":
    demo()
