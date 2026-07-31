"""The run modes: one-shot check, long-running server, test and report."""

import datetime as dt
import random
import sys
import time

import requests

from .bms import fetch, scan, shows_for
from .config import *
from .messages import alert_text, live_report, pretty_date, shift_report
from .shifts import maybe_report_shift, shift_at
from .state import load_state, save_state
from .telegram import answer, poll_commands, send_telegram


def run_cycle(state, session, jitter=False):
    """One full check: shift boundary, scan, alert, tally. Saves state.

    Shared by the one-shot run and the long-running server, so both behave
    identically - only how often they are called differs.
    """
    seen = set(state["seen"])
    now = dt.datetime.now(IST)
    tally = maybe_report_shift(state)

    if jitter:
        # Cron fires on the exact tick for everyone; a random offset keeps this
        # request out of the top-of-the-minute crowd.
        time.sleep(random.uniform(0, 45))

    hits, broken, bookable = scan(session)
    all_keys = {k for date_shows in hits.values() for k, _ in date_shows}
    fresh = [(dc, s) for dc in DATES for k, s in hits.get(dc, []) if k not in seen]

    if tally:
        if bookable:
            tally["bookable"] = bookable
        for date_code in DATES:
            tally["found"][date_code] = max(len(hits.get(date_code, [])),
                                            tally["found"].get(date_code, 0))
        tally["checks"] += 1
        tally["errors"] += len(broken)
        stamp = now.strftime("%I:%M %p").lstrip("0")
        tally["first"] = tally["first"] or stamp
        tally["last"] = stamp

    if fresh:
        by_date = {}
        for date_code, show in fresh:
            by_date.setdefault(date_code, []).append(show)
        send_telegram(alert_text(by_date))
    else:
        print("nothing new")

    state["seen"] = sorted(all_keys | seen)
    state["shift"] = tally
    save_state(state)
    return hits, broken, bookable, tally


def main():
    """One-shot check, for cron or Task Scheduler."""
    state = load_state()
    session = requests.Session()
    hits, broken, bookable, tally = run_cycle(state, session, jitter=True)

    # Anything typed into the chat since the last run. Uses the scan above, so
    # asking for a report costs BookMyShow no extra requests.
    for cmd in poll_commands(state):
        answer(cmd, hits, broken, bookable, tally)
    save_state(state)

    if broken:
        # Loud on purpose. "Couldn't reach BMS" looks exactly like "not open yet"
        # from the outside, and silently watching nothing is the one failure that
        # loses the tickets. Non-zero exit -> GitHub marks the run failed and mails you.
        sys.exit("could not reach BMS for %s - watcher is NOT working" % ", ".join(broken))


def report_now():
    """Send a status report for right now, on demand. Does not touch seen.json."""
    hits, broken, bookable = scan(requests.Session())
    send_telegram(live_report(hits, broken, bookable))
    print("report sent")


def test_run():
    """Manual smoke test: prove the whole chain end to end, right now.

    A strict test would find no 4DX 3D shows (none are scheduled anywhere yet),
    send nothing, and prove nothing. So this looks at dates that ARE open and
    relaxes the format filter if it has to, guaranteeing a real message arrives.
    Never touches seen.json, so it cannot suppress the real alert later.
    """
    global FORMAT
    wanted = FORMAT
    session = requests.Session()
    # IST, not the runner's clock: on a UTC machine date.today() is yesterday
    # for most of the Indian evening, and BMS dates are Indian dates.
    today = dt.datetime.now(IST).strftime("%Y%m%d")
    probe = fetch(session, today)
    if probe is None:
        sys.exit("TEST FAILED: could not reach BMS - the watcher would not work")

    # Scan open dates until real matching shows turn up - the first open date
    # often has none in this format, which would make for a useless test.
    open_dates = [d["DateCode"] for d in probe.get("ShowDatesArray", [])
                  if not d.get("isDisabled")][:6] or [today]
    print("open dates on BMS: %s" % open_dates)
    payloads, dates, hits = {today: probe}, [], []
    for dc in open_dates:
        if dc not in payloads:
            time.sleep(random.uniform(4, 11))
            payloads[dc] = fetch(session, dc)
        dates.append(dc)
        found = [(dc, t) for _, t in shows_for(payloads.get(dc), dc)]
        print("  %s: %d matching shows" % (dc, len(found)))
        hits += found
        if hits:
            break

    relaxed = False
    if not hits:
        FORMAT, relaxed = "", True      # nothing in this format anywhere - show something real
        hits = [(dc, t) for dc in dates for _, t in shows_for(payloads.get(dc), dc)]

    FORMAT = wanted     # restore, so the preview below shows the real filter
    lines = ["✅ TEST PASSED - alerts will reach this chat! 📲",
             "",
             "🎯 WHAT I'M WATCHING FOR YOU",
             "  🍿 Movie:  %s" % MOVIE_NAME,
             "  🕶️ Format: %s %s ONLY" % (LANGUAGE, wanted),
             "  📅 Dates:  %s" % " and ".join(pretty_date(d) for d in DATES),
             "  🕒 Shows:  starting between %s and %s" % (TIME_FROM, TIME_TO),
             "  📍 City:   %s" % REGION_CODE,
             "",
             "🔒 Those dates are NOT open on BookMyShow yet.",
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
        lines.append("No shows found at all - check EVENT_CODE and REGION_CODE.")
    lines.append("- - - - - - - - - - - - - - - - - - - -")
    lines.append("☝️ end of preview - the real one lands on 8-9 Aug 🤞")
    send_telegram("\n".join(lines))
    print("test message sent; seen.json untouched")


def serve():
    """Stay running: instant chat replies, BMS scan on the usual 10-min rhythm.

    Two different clocks on purpose. Chat messages are answered the second they
    arrive, via a long poll that Telegram holds open. BookMyShow is still only
    scanned every SCAN_EVERY seconds - replying fast must not mean hammering
    them. A reply between scans uses the last scan's data, which is at most one
    interval old and is exactly what a scheduled run would have reported.
    """
    print("serving: chat replies are instant, BMS scanned every %d min. Ctrl-C to stop."
          % (SCAN_EVERY // 60))
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
                tally = maybe_report_shift(state)

            # Blocks here until you type something or the long poll times out,
            # so a reply costs no polling and arrives in about a second.
            for cmd in poll_commands(state, wait=LONG_POLL):
                answer(cmd, hits, broken, bookable, tally)
            save_state(state)
        except KeyboardInterrupt:
            print("stopped")
            return
        except Exception as e:      # a daemon must not die on one bad iteration
            print("cycle error: %s: %s" % (type(e).__name__, e))
            time.sleep(30)
