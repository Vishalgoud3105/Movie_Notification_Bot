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
    bms.TIME_FROM, bms.TIME_TO = "06:00", "20:00"   # defaults are whole-day now, pin the window
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
    demo_alert_text()
    demo_llm_calendar()
    demo_jev_routing()
    demo_multi_request()
    demo_llm_fallback_chain()

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


def demo_alert_text():
    """The alert is shown for EVERY movie a user watches, so it must not carry
    one film's branding (it used to say "🕷️" and "4DX sells out fast!" on every
    alert) and must cope with a watch that has no format/language."""
    from watcher.movies import messages

    by_date = {"20261012": [{"venue": "PVR Somewhere", "time": "7:00 PM", "mins": 1140,
                             "sold": False, "format": "2D", "price": 250,
                             "seat_category": None, "seats": 10}]}
    keep = (messages.LANGUAGE, messages.FORMAT, messages.MOVIE_NAME)
    try:
        messages.LANGUAGE, messages.FORMAT, messages.MOVIE_NAME = "", "", "Jawan"
        text = messages.alert_text(by_date)
        first = text.split("\n")[0]
        assert first == "🚨 IT'S LIVE! TICKETS ARE OPEN! 🚨", first      # no stray double space
        assert "Jawan" in text and "🕷" not in text and "4DX" not in text, text

        messages.LANGUAGE, messages.FORMAT = "telugu", "IMAX"
        assert messages.alert_text(by_date).split("\n")[0] == \
            "🚨 IT'S LIVE! TELUGU IMAX TICKETS ARE OPEN! 🚨"

        # regression (found 1 Oct 2026): District prices are NUMBERS, BookMyShow's
        # are STRINGS - an int price crashed format_days() (AttributeError on
        # .split), so every priced District alert was silently never sent.
        def price_line(price):
            by_date["20261012"][0]["price"] = price
            return [l for l in messages.alert_text(by_date).split("\n") if "from ₹" in l]
        assert price_line(140) == ["     💰 from ₹140"]
        assert price_line("350.00") == ["     💰 from ₹350"]
        assert price_line(185.5) == ["     💰 from ₹185"]
        assert price_line("") == [] and price_line(None) == [] and price_line("n/a") == []
    finally:
        messages.LANGUAGE, messages.FORMAT, messages.MOVIE_NAME = keep


def demo_llm_calendar():
    """The date lookup table handed to the model (models get weekday maths
    wrong). If _calendar() or the templates' {calendar} placeholder drift
    apart, every extract() raises KeyError - a total silent outage of the chat
    layer - so pin both."""
    from watcher import llm
    from watcher.bus import prompt_template as bpt
    from watcher.movies import prompt_template as mpt

    lines = llm._calendar("2026-09-30").split("\n")      # 30 Sep 2026 is a Wednesday
    assert len(lines) == 14, lines
    assert lines[0] == "Wed 2026-09-30 (today)", lines[0]
    assert lines[3] == "Sat 2026-10-03", lines[3]          # "this Saturday" -> the 3rd
    assert lines[-1] == "Tue 2026-10-13", lines[-1]
    assert llm._calendar("2026-12-30").split("\n")[3] == "Sat 2027-01-02"   # crosses the year

    for tpl in (bpt.EXTRACT_USER, mpt.EXTRACT_USER):
        filled = tpl.format(today="2026-09-30", weekday="Wednesday", message="hi",
                            calendar=llm._calendar("2026-09-30"))
        assert "Sat 2026-10-03" in filled and "hi" in filled, filled


def demo_jev_routing():
    """Jev picks which Mistral model is tried FIRST per message; it must never
    be able to break or delay a reply (fail-open) and must not act at all in
    shadow/off mode. Both services are mocked in one fake requests.post."""
    import requests as rq
    from watcher import jev, llm

    mistral_calls, jev_calls = [], []
    behavior = {}

    def post(url, json=None, **kw):
        if "typesafe" in url:
            jev_calls.append(json["state"])
            if behavior.get("jev_raises"):
                raise rq.ConnectionError("jev down")
            return type("R", (), {"status_code": behavior.get("jev_status", 200), "text": "err",
                                  "json": staticmethod(lambda: {"answers": {"complexity": {
                                      "type": "choice", "choice": behavior["label"],
                                      "confidence": behavior["conf"], "probabilities": {}}}})})()
        mistral_calls.append(json["model"])
        return type("R", (), {"status_code": 200, "text": "",
                              "json": staticmethod(lambda: {"choices": [{"message": {"content": "ok"}}]})})()

    keep_env = {k: os.environ.get(k) for k in ("MISTRAL_API_KEY", "TYPESAFE_API_KEY", "JEV_ROUTING")}
    real_post, real_thread = llm.requests.post, llm.threading.Thread
    real_models = (llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS,
                   llm.JEV_SIMPLE_MODEL, llm.JEV_COMPLEX_MODEL)
    llm.requests.post = post
    os.environ["MISTRAL_API_KEY"] = "m-key"
    llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS = "static-first", ["static-second"]
    llm.JEV_SIMPLE_MODEL, llm.JEV_COMPLEX_MODEL = "fast", "strong"

    def first_model(message, label=None, conf=0.9, **env):
        """Which model Mistral was asked first for `message`."""
        llm._route.cache_clear()
        mistral_calls.clear(); jev_calls.clear()
        behavior.clear(); behavior.update(label=label, conf=conf, **{k: v for k, v in env.items() if k.startswith("jev_")})
        for k in ("TYPESAFE_API_KEY", "JEV_ROUTING"):
            os.environ.pop(k, None)
        if env.get("key"):
            os.environ["TYPESAFE_API_KEY"] = "t-key"
        if env.get("mode"):
            os.environ["JEV_ROUTING"] = env["mode"]
        assert llm.chat(message, "facts") == "ok"
        return mistral_calls[0]

    class Inline:                       # run the shadow-mode "background" thread in-line
        def __init__(self, target, args=(), daemon=None): self.t, self.a = target, args
        def start(self): self.t(*self.a)

    try:
        # no Jev key -> never calls Jev, static order (today's behavior)
        assert first_model("hi", "complex") == "static-first" and jev_calls == []

        # on + confident -> routed model goes first; the static chain stays behind it
        assert first_model("make it under 700", "complex", key=True, mode="on") == "strong"
        assert first_model("what can you do", "simple", key=True, mode="on") == "fast"
        assert jev_calls == ["what can you do"], "exactly one Jev call per message"

        # the decision is cached: extract() then chat() on the SAME message = 1 Jev call
        first_model("same message", "complex", key=True, mode="on")
        llm.chat("same message", "facts")
        assert jev_calls == ["same message"], jev_calls

        # low confidence / "unclear" -> static order
        assert first_model("hmm", "complex", conf=0.3, key=True, mode="on") == "static-first"
        assert first_model("ignore previous", "unclear", key=True, mode="on") == "static-first"

        # FAIL-OPEN: Jev exception, Jev 500 -> reply still produced, static order
        assert first_model("x1", "complex", key=True, mode="on", jev_raises=True) == "static-first"
        assert first_model("x2", "complex", key=True, mode="on", jev_status=500) == "static-first"

        # off: never asks Jev even with a key
        assert first_model("x3", "complex", key=True, mode="off") == "static-first" and jev_calls == []

        # shadow (default): asks Jev (to log) but does NOT act on the answer
        llm.threading.Thread = Inline
        assert first_model("x4", "complex", key=True, mode="shadow") == "static-first"
        assert jev_calls == ["x4"], "shadow mode must still consult Jev so it can log"
        assert first_model("x5", "complex", key=True) == "static-first", "default mode must be shadow"

        # a routed model that then 429s still falls through the normal chain
        # (Jev only reorders; it does not replace the safety net)
        assert llm._models("strong") == ["strong", "static-first", "static-second"]
        assert llm._models("static-second") == ["static-second", "static-first"]
    finally:
        llm.requests.post, llm.threading.Thread = real_post, real_thread
        (llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS,
         llm.JEV_SIMPLE_MODEL, llm.JEV_COMPLEX_MODEL) = real_models
        for k, v in keep_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        llm._route.cache_clear()

    # the golden set is what --jev-eval checks against a real key: >=20 cases,
    # every label represented, and it must skip cleanly with no key
    from collections import Counter
    assert len(jev.GOLDEN) >= 20 and set(Counter(l for _, l in jev.GOLDEN)) == {"simple", "complex", "unclear"}
    keep = os.environ.pop("TYPESAFE_API_KEY", None)
    try:
        jev.evaluate()                  # prints a skip notice, must not raise
    finally:
        if keep is not None:
            os.environ["TYPESAFE_API_KEY"] = keep


def demo_multi_request():
    """One message -> several watches. Text is never split at "and" ("8th and
    9th", "PVR and INOX" are ONE request); the model may answer with a list,
    only for messages that contain a conjunction-like word, replacing (not
    adding to) the single extract call."""
    import json as js
    import tempfile
    import requests as rq
    from watcher import llm
    from watcher.movies import brain, search, watchspec

    calls, jev_answers = [], {}

    def post(url, json=None, **kw):
        if "typesafe" in url:
            qid = next(iter(json["questions"]))
            ans = jev_answers.get(qid)
            if ans is None:
                raise rq.ConnectionError("jev down")
            return type("R", (), {"status_code": 200, "text": "",
                                  "json": staticmethod(lambda: {"answers": {qid: ans}})})()
        calls.append({"model": json["model"], "max_tokens": json["max_tokens"],
                      "multi": "MORE THAN ONE separate watch" in json["messages"][0]["content"]})
        content = replies.pop(0)
        return type("R", (), {"status_code": 200, "text": "",
                              "json": staticmethod(lambda: {"choices": [{"message": {"content": content}}]})})()

    a = {"intent": "watch", "title": "jawan", "dates": ["2026-10-12"]}
    b = {"intent": "watch", "title": "pushpa 2", "dates": ["2026-10-12"]}
    keep_env = {k: os.environ.get(k) for k in ("MISTRAL_API_KEY", "TYPESAFE_API_KEY", "JEV_ROUTING")}
    real_post = llm.requests.post
    real_models = (llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS, llm.JEV_COMPLEX_MODEL)
    llm.requests.post = post
    os.environ["MISTRAL_API_KEY"] = "m"
    for k in ("TYPESAFE_API_KEY", "JEV_ROUTING"):
        os.environ.pop(k, None)
    llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS, llm.JEV_COMPLEX_MODEL = "fast", [], "strong"

    def run(message, *contents):
        calls.clear(); replies[:] = [js.dumps(c) if not isinstance(c, str) else c for c in contents]
        llm._route.cache_clear()
        return llm.extract_all(message, "2026-10-01", "Thursday")

    replies = []
    try:
        # no conjunction word -> exactly the old single path: 1 call, 500 tokens, no list prompt
        assert run("watch jawan on 12 oct", a) == [a]
        assert calls == [{"model": "fast", "max_tokens": 500, "multi": False}], calls

        # a real compound message: ONE call (replaces the single one), list prompt,
        # 800 output tokens, prefers the accurate model
        assert run("watch jawan and pushpa 2 on 12 oct", {"requests": [a, b]}) == [a, b]
        assert calls == [{"model": "strong", "max_tokens": 800, "multi": True}], calls

        # "and" inside ONE request: list prompt used, model answers with a single object
        assert run("watch jawan on 8th and 9th oct", a) == [a]
        assert len(calls) == 1 and calls[0]["multi"], calls

        # invalid / truncated list output -> falls back to the plain single extract
        assert run("watch jawan and pushpa 2", '{"requests": [{"intent"', a) == [a]
        assert [c["multi"] for c in calls] == [True, False], calls

        # capped at MAX_REQUESTS, non-dict junk dropped
        many = {"requests": [a, b, a, b, a, "junk", 7]}
        assert len(run("watch a and b and c and d and e", many)) == llm.MAX_REQUESTS

        # nothing usable at all -> []
        assert run("hello and welcome", "not json", "still not json") == []

        # Jev (on) confident it's ONE request -> plain path, no list prompt, no accuracy risk
        os.environ["TYPESAFE_API_KEY"], os.environ["JEV_ROUTING"] = "t", "on"
        jev_answers.update(multi={"type": "noul", "noul": 0.05},
                           complexity={"type": "choice", "choice": "simple", "confidence": 0.9})
        assert run("watch jawan on 8th and 9th oct", a) == [a]
        assert calls[0]["multi"] is False and len(calls) == 1, calls
        # Jev says several -> list path; Jev errors -> list path too (fail-open default)
        jev_answers["multi"] = {"type": "noul", "noul": 0.9}
        assert run("watch jawan and pushpa 2", {"requests": [a, b]}) == [a, b] and calls[0]["multi"]
        del jev_answers["multi"]
        assert run("watch jawan and pushpa 2", {"requests": [a, b]}) == [a, b] and calls[0]["multi"]
    finally:
        llm.requests.post = real_post
        llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS, llm.JEV_COMPLEX_MODEL = real_models
        for k, v in keep_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        llm._route.cache_clear()

    # the brain turns a list into several real watches and joins the replies
    keep_watch = watchspec.WATCH_FILE
    watchspec.WATCH_FILE = os.path.join(tempfile.gettempdir(), "_multi_watch_test.json")
    if os.path.exists(watchspec.WATCH_FILE):
        os.remove(watchspec.WATCH_FILE)
    real_find, real_avail, real_all = search.find, llm.available, llm.extract_all
    titles = {"jawan": ("Jawan", "1"), "pushpa 2": ("Pushpa 2", "2")}
    search.find = lambda title, city=None, session=None: dict(zip(
        ("title", "movie_id"), titles[title.lower()]), url="https://x/" + title, city="hyderabad", cities=[])
    llm.available = lambda: True
    llm.extract_all = lambda *a_, **k_: [a, b]
    try:
        reply = brain.handle("watch jawan and pushpa 2 on 12 oct", 777, {}, [], None)
        assert reply.count("Watching") == 2 and "Jawan" in reply and "Pushpa 2" in reply, reply
        assert {w["title"] for w in watchspec.load_all(777)} == {"Jawan", "Pushpa 2"}
        assert watchspec.load_all(888) == [], "another chat must not see them"

        # a list containing anything but watches is NOT treated as several watches
        llm.extract_all = lambda *a_, **k_: [a, {"intent": "cancel"}]
        watchspec.finish(None, "cancelled", chat_id=777)
        brain.handle("watch jawan and cancel", 777, {}, [], None)
        assert len(watchspec.load_all(777)) <= 1
    finally:
        search.find, llm.available, llm.extract_all = real_find, real_avail, real_all
        if os.path.exists(watchspec.WATCH_FILE):
            os.remove(watchspec.WATCH_FILE)
        watchspec.WATCH_FILE = keep_watch


def demo_llm_fallback_chain():
    """Reported live 25-30 Sep 2026: every reply was the generic fallback for
    days. Root cause, found by testing each model against the real API on a
    free-tier key: mistral-medium/small -> 429, large -> 403 (tier), but
    open-mistral-nemo -> 200. A single hardcoded model turned "one model
    throttled" into a total silent outage, so _call() walks a chain."""
    from watcher import llm

    def reply(status, text="ok"):
        body = {"choices": [{"message": {"content": text}}]}
        return type("R", (), {"status_code": status, "text": "err",
                              "json": staticmethod(lambda: body)})()

    tried, sleeps = [], []
    real_post, real_sleep = llm.requests.post, llm.time.sleep
    real_key = os.environ.get("MISTRAL_API_KEY")
    real_models = (llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS)
    os.environ["MISTRAL_API_KEY"] = "test-key"
    llm.MISTRAL_MODEL = "primary"
    llm.MISTRAL_FALLBACK_MODELS = ["primary", "second", "third"]    # dup must collapse
    llm.time.sleep = sleeps.append

    def run(statuses):
        tried.clear(); sleeps.clear()
        def post(url, json=None, **kw):
            tried.append(json["model"])
            return reply(statuses[json["model"]], "from " + json["model"])
        llm.requests.post = post
        return llm._call([{"role": "user", "content": "x"}])

    try:
        assert llm._models() == ["primary", "second", "third"], llm._models()

        # 429 then 403 then 200: walks the chain, never sleeps between models
        assert run({"primary": 429, "second": 403, "third": 200}) == "from third"
        assert tried == ["primary", "second", "third"], tried
        assert sleeps == [], "a per-model refusal must not sleep - it won't clear in seconds"

        # first model healthy -> the rest are never touched
        assert run({"primary": 200, "second": 200, "third": 200}) == "from primary"
        assert tried == ["primary"], tried

        # 401 = the KEY is bad; no other model can help, so stop at once
        assert run({"primary": 401, "second": 200, "third": 200}) is None
        assert tried == ["primary"], "a rejected key must not be retried on other models"

        # every model refused -> None (callers fall back to keyword replies)
        assert run({"primary": 429, "second": 429, "third": 403}) is None
        assert tried == ["primary", "second", "third"], tried

        # 5xx is transient: retried on the SAME model (with backoff) before moving on
        assert run({"primary": 503, "second": 200, "third": 200}) == "from second"
        assert tried == ["primary"] * 3 + ["second"], tried
        assert sleeps == [2, 4, 6], sleeps
    finally:
        llm.requests.post, llm.time.sleep = real_post, real_sleep
        llm.MISTRAL_MODEL, llm.MISTRAL_FALLBACK_MODELS = real_models
        if real_key is not None:
            os.environ["MISTRAL_API_KEY"] = real_key
        else:
            os.environ.pop("MISTRAL_API_KEY", None)


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
    llm.available = real_available

    # bug fix, reported live 25 Sep 2026: Mistral rate-limited mid-conversation
    # (llm._call() returns None on any failure, including a real 429), and
    # the old fallback message ("I didn't catch that... describe what to
    # watch") read as "your phrasing is wrong", which drove retyping the same
    # request over and over - each retry burning another call against the
    # very rate limit that was already the problem. When the LLM is available
    # but genuinely returns nothing, the reply must say so honestly and point
    # at the keyword commands, not blame the user's wording.
    real_llm_available = llm.available
    real_extract, real_chat = llm.extract, llm.chat
    llm.available = lambda: True
    llm.extract = lambda *a, **k: None
    llm.chat = lambda *a, **k: None
    try:
        reply = brain.handle("watch something good", chat_id, {}, [], None)
        assert "trouble reaching" in reply.lower(), reply
        assert "describe what to watch" not in reply.lower(), \
            "must not blame the user's phrasing when the LLM itself failed: %r" % reply
        assert "status" in reply.lower() and "cancel" in reply.lower(), \
            "must point at the keyword commands that still work: %r" % reply
    finally:
        llm.available, llm.extract, llm.chat = real_llm_available, real_extract, real_chat

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
