"""The run modes: one-shot check, long-running server, test and report."""

import datetime as dt
import random
import sys
import time

import requests

from .bms import fetch, shows_for
from .sources import scan
from .config import *
from .messages import alert_text, live_report, pretty_date, shift_report
from .shifts import maybe_report_shift, shift_at
from .state import load_state, save_state
from ..telegram import poll_commands, reply_to, send_telegram, wants_report


def answer(chat_id, cmd, hits, broken, bookable, tally=None):
    """Reply to one chat message about the movie watch(es) - to `chat_id`
    only, never broadcast. A show-open alert is likewise scoped to whichever
    chat started that watch (see _send_to_owning_chat()); only the shift
    report still broadcasts to every known chat.

    Tries the brain (LLM + watch handling) first, but any failure there must
    still leave you with a working status command, so it falls through to the
    keyword behaviour rather than raising.
    """
    print("command from you: %s" % cmd)
    try:
        from .brain import handle
        reply = handle(cmd, chat_id, hits, broken, bookable, tally)
        if reply:
            reply_to(chat_id, reply)
            return
    except Exception as e:                      # never let chat break the watcher
        print("brain failed, falling back to keywords: %s: %s" % (type(e).__name__, e))

    if wants_report(cmd):
        reply_to(chat_id, live_report(hits, broken, bookable, chat_id,
                                      checks=(tally or {}).get("checks", 1)))
    else:
        reply_to(chat_id, "\n".join([
            "🤖 I'm here to watch movies for you.",
            "",
            "I'm not an AI - I just look for a keyword. Say any of:",
            "  report · status · check · update · news",
            "with or without a /, in any sentence.",
            "  e.g. \"report\", \"/status\", \"any update?\"",
            "",
            "You'll also get a report at the end of each shift,",
            "and one 🚨 alert the moment what you're watching opens."]))


def _send_to_owning_chat(spec, text):
    """A show-open alert reaches only the chat that started this watch -
    never every chat the bot is in. Mirrors watcher/bus/runner.py's identical
    helper and the same reported bug: watching a movie from the owner's DM
    was alerting into every group too, because alerts went through
    send_telegram() (broadcast) unconditionally. Falls back to a broadcast
    only for a watch persisted before chat scoping existed (no "chat_id"
    field at all) so an already-running pre-migration watch doesn't go
    silent."""
    chat_id = spec.get("chat_id")
    if chat_id is not None:
        return reply_to(chat_id, text)
    return send_telegram(text)


def boot():
    """Log what's currently being watched.

    Nothing to push into globals here anymore - run_cycle() applies each
    active spec fresh, in turn, every cycle (see watchspec.py's docstring),
    so there's no persistent "current" global state to resume into at
    process start the way the old single-watch design needed.
    """
    from . import watchspec
    from .messages import pretty_spec
    watches = watchspec.load_all()
    for w in watches:
        print("resuming watch: %s" % pretty_spec(w))
    return watches


def run_cycle(state, session, jitter=False):
    """One full check: shift boundary, scan every active movie, alert, tally.
    Saves state. Shared by the one-shot run and the long-running server, so
    both behave identically - only how often they are called differs.

    Loops over every active watch (watchspec.load_all()), pushing each one's
    fields onto the live globals in turn (watchspec.apply(spec)) right before
    scanning it - see watchspec.py's module docstring for why this reuses the
    single-spec-push machinery in a loop rather than rewriting bms.py/
    district.py to take an explicit spec argument. Scans nothing at all until
    at least one movie has actually been set to watch by chat - there is no
    standing default anymore, same reasoning bus/runner.py has always used.

    Seen-show keys are namespaced "<watch_id>|<key>" so two different movies
    can never collide in the shared dedup set even if they coincidentally
    produce the same composite key.

    Returns (hits, broken, bookable, tally) where hits is now
    {watch_id: {date_code: [(key, show), ...]}} rather than a flat per-date
    dict, since more than one movie can be active - see brain.py::_facts()
    and messages.py::live_report() for how callers read the new shape.
    """
    from . import watchspec
    specs = watchspec.load_all()
    tally = maybe_report_shift(state, watching=bool(specs))

    if not specs:
        state["shift"] = tally
        save_state(state)
        return {}, [], None, tally

    if jitter:
        # Cron fires on the exact tick for everyone; a random offset keeps this
        # request out of the top-of-the-minute crowd.
        time.sleep(random.uniform(0, 45))

    seen = set(state["seen"])
    combined_hits, all_broken, all_bookable = {}, [], None

    for spec in specs:
        watchspec.apply(spec)
        wid = spec["id"]
        now = dt.datetime.now(IST)

        hits, broken, bookable = scan(session)
        combined_hits[wid] = hits
        all_broken += broken
        if bookable:
            all_bookable = bookable if not all_bookable else max(all_bookable, bookable)

        if tally is not None:
            entry = tally["watches"].setdefault(wid, {"title": spec.get("title"), "found": {}})
            for date_code in DATES:
                entry["found"][date_code] = len(hits.get(date_code, []))
            if bookable:
                tally["bookable"] = all_bookable
            tally["checks"] += 1
            tally["errors"] += len(broken)
            stamp = now.strftime("%I:%M %p").lstrip("0")
            tally["first"] = tally["first"] or stamp
            tally["last"] = stamp

        all_keys = {k for date_shows in hits.values() for k, _ in date_shows}
        fresh = [(dc, s) for dc in DATES for k, s in hits.get(dc, []) if (wid + "|" + k) not in seen]

        delivered = True
        if fresh:
            by_date = {}
            for date_code, show in fresh:
                by_date.setdefault(date_code, []).append(show)
            delivered = _send_to_owning_chat(spec, alert_text(by_date))
            if not delivered:
                print("ALERT NOT DELIVERED for %s - not marking these shows as "
                      "seen, so the next cycle tries again" % spec.get("title"))
        else:
            print("nothing new for %s" % spec.get("title"))

        # Only remember shows once the alert for them actually went out. Marking
        # them seen on a failed send would drop the one message that matters and
        # never retry it.
        if delivered:
            seen |= {wid + "|" + k for k in all_keys}

        # Goal reached: every watched date now has shows and the alert went
        # out, so release JUST this watch and keep scanning the rest.
        if delivered and fresh and all(hits.get(d) for d in DATES):
            watchspec.finish(wid, "found")
            _send_to_owning_chat(spec, "✅ %s - that's everything I was watching for! "
                                 "Alert sent above. Tell me what's next." % spec.get("title"))

    state["seen"] = sorted(seen)
    state["shift"] = tally
    save_state(state)
    return combined_hits, all_broken, all_bookable, tally


def main():
    """One-shot check, for cron or Task Scheduler."""
    boot()
    state = load_state()
    session = requests.Session()
    hits, broken, bookable, tally = run_cycle(state, session, jitter=True)

    # Anything typed into the chat since the last run. Uses the scan above, so
    # asking for a report costs BookMyShow no extra requests.
    for chat_id, cmd in poll_commands(state):
        answer(chat_id, cmd, hits, broken, bookable, tally)
    save_state(state)

    if broken:
        # Loud on purpose. "Couldn't reach the site" looks exactly like "not open yet"
        # from the outside, and silently watching nothing is the one failure that
        # loses the tickets. Non-zero exit -> GitHub marks the run failed and mails you.
        sys.exit("could not reach %s for %s - watcher is NOT working" % (SITE, ", ".join(broken)))


def report_now():
    """Send a status report for right now, on demand. Does not touch seen.json."""
    boot()
    hits, broken, bookable = scan(requests.Session())
    send_telegram(live_report(hits, broken, bookable))
    print("report sent")


def test_run():
    """Manual smoke test: prove the whole chain end to end, right now.

    A strict test would find no 4DX 3D shows (none are scheduled anywhere yet),
    send nothing, and prove nothing. So this looks at dates that ARE open and
    relaxes the format filter if it has to, guaranteeing a real message arrives.
    Never touches seen.json, so it cannot suppress the real alert later.

    Uses the .env-configured field values directly (FORMAT/DATES/MOVIE_NAME/...),
    not any chat-set watch - this is a standalone diagnostic probe, run as its
    own CLI mode (--test), never interleaved with run_cycle()'s per-spec loop.
    """
    boot()
    from . import district
    use_district = SOURCE == "district"
    get = district.fetch if use_district else fetch
    parse = district.shows_for if use_district else shows_for

    global FORMAT
    wanted = FORMAT
    session = requests.Session()
    # IST, not the host clock: on a UTC machine date.today() is yesterday for
    # most of the Indian evening, and showtime dates are Indian dates.
    today = dt.datetime.now(IST).strftime("%Y%m%d")
    probe = get(session, today)
    if probe is None:
        sys.exit("TEST FAILED: could not reach %s - the watcher would not work" % SITE)

    if use_district:
        open_dates = [d.replace("-", "") for d in district.show_dates(probe)][:6] or [today]
    else:
        open_dates = [d["DateCode"] for d in probe.get("ShowDatesArray", [])
                      if not d.get("isDisabled")][:6] or [today]

    # Scan open dates until real matching shows turn up - the first open date
    # often has none in this format, which would make for a useless test.
    print("open dates on %s: %s" % (SITE, open_dates))
    payloads, dates, hits = {today: probe}, [], []
    for dc in open_dates:
        if dc not in payloads:
            time.sleep(random.uniform(4, 11))
            payloads[dc] = get(session, dc)
        dates.append(dc)
        found = [(dc, t) for _, t in parse(payloads.get(dc), dc)]
        print("  %s: %d matching shows" % (dc, len(found)))
        hits += found
        if hits:
            break

    relaxed = False
    if not hits:
        FORMAT, relaxed = "", True      # nothing in this format anywhere - show something real
        if use_district:
            district.FORMAT = ""
        hits = [(dc, t) for dc in dates for _, t in parse(payloads.get(dc), dc)]
        if use_district:
            district.FORMAT = wanted

    FORMAT = wanted     # restore, so the preview below shows the real filter
    lines = ["✅ TEST PASSED - alerts will reach this chat! 📲",
             "",
             "🎯 WHAT I'M WATCHING FOR YOU",
             "  🍿 Movie:  %s" % MOVIE_NAME,
             "  🕶️ Format: %s %s ONLY" % (LANGUAGE, wanted),
             "  📅 Dates:  %s" % " and ".join(pretty_date(d) for d in DATES),
             "  🕒 Shows:  starting between %s and %s" % (TIME_FROM, TIME_TO),
             "  📍 Venue:  %s" % (", ".join(v.title() for v in VENUES) or "any cinema"),
             "",
             "🔒 No %s shows on those dates yet." % wanted,
             "👀 I'm checking every few minutes and will ping you ONCE,",
             "   the very moment they open. Sit back.",
             "",
             "👇👇 EXACTLY what that alert will look like 👇👇",
             "(real data, from %s - a date that's already open)" % pretty_date(dates[-1])]
    if relaxed:
        lines.append("⚠️ No %s shows exist on any open date right now, so this" % wanted)
        lines.append("preview falls back to other formats just to prove delivery.")
    lines.append("")
    lines.append("- - - - - - - - - - - - - - - - - - - -")
    if hits:
        by_date = {}
        for dc, show in hits:
            by_date.setdefault(dc, []).append(show)
        lines.append(alert_text(by_date))
    else:
        lines.append("No shows found at all - check DISTRICT_URL and VENUES.")
    lines.append("- - - - - - - - - - - - - - - - - - - -")
    lines.append("☝️ end of preview - the real one lands on 8-9 Aug 🤞")
    send_telegram("\n".join(lines))
    print("test message sent; seen.json untouched")


def serve():
    """Stay running: instant chat replies, site scan on the usual 10-min rhythm.

    Two different clocks on purpose. Chat messages are answered the second they
    arrive, via a long poll that Telegram holds open. BookMyShow is still only
    scanned every SCAN_EVERY seconds - replying fast must not mean hammering
    them. A reply between scans uses the last scan's data, which is at most one
    interval old and is exactly what a scheduled run would have reported.
    """
    boot()
    print("serving: chat replies are instant, %s scanned every %d min for "
          "whatever's being watched. Ctrl-C to stop." % (SITE, SCAN_EVERY // 60))
    state = load_state()
    session = requests.Session()
    hits, broken, bookable, tally = {}, [], None, None
    next_scan = 0.0

    while True:
        try:
            if time.time() >= next_scan:
                hits, broken, bookable, tally = run_cycle(state, session)
                next_scan = time.time() + SCAN_EVERY
            else:
                # Costs nothing when no shift ended, so the report lands on the
                # boundary instead of waiting for the next scan.
                from . import watchspec
                tally = maybe_report_shift(state, watching=bool(watchspec.load_all()))

            # Blocks here until you type something or the long poll times out,
            # so a reply costs no polling and arrives in about a second.
            for chat_id, cmd in poll_commands(state, wait=LONG_POLL):
                answer(chat_id, cmd, hits, broken, bookable, tally)
            save_state(state)
        except KeyboardInterrupt:
            print("stopped")
            return
        except Exception as e:      # a daemon must not die on one bad iteration
            print("cycle error: %s: %s" % (type(e).__name__, e))
            time.sleep(30)
