"""Everything the bot says: alerts, shift reports, venue and date formatting."""

import datetime as dt
import random

from .config import *

# Rotated rather than hardcoded to one film: these back the idle/fallback
# replies, which must work with Groq down (see brain.py's routing order) and
# so can't be LLM-phrased like onboarding.py's welcome message is - but
# always showing the same Spider-Man example read as a canned template.
_EXAMPLES = [
    "watch Spider-Man 4DX 3D at Irrum Manzil on 8 and 9 Aug, morning to evening",
    "watch Pushpa 2 IMAX at Forum Sujana Mall this Friday evening",
    "watch the new Marvel movie in English at PVR Nizampet this weekend",
    "watch Kalki 2898 AD 3D in Hyderabad on 25 Aug, afternoon to night",
    "watch Jawan in Telugu at AMB Cinemas next Saturday",
]


def example_watch_phrase():
    return random.choice(_EXAMPLES)


def short_venue(name):
    """'PVR Superplex Inorbit: LUXE, PXL, 4DX: Cyberabad' -> 'PVR Superplex Inorbit, Cyberabad'."""
    parts = []
    for chunk in name.split(":"):
        chunk = chunk.strip()
        # drop chunks that are nothing but screen-brand names
        if chunk and not all(w.strip().upper() in SCREEN_WORDS for w in chunk.split(",") if w.strip()):
            parts.append(chunk)
    # "PVR" + "Irrum Manzil" reads as one name, not two: merge a short brand prefix
    if len(parts) > 1 and len(parts[0]) <= 4:
        parts[:2] = ["%s %s" % (parts[0], parts[1])]
    out = ", ".join(parts) if parts else name
    for tail in (", Hyderabad", " Hyderabad"):
        if out.endswith(tail):
            out = out[: -len(tail)]
    return out.strip(" ,")


def pretty_spec(spec):
    """Human summary of a watch spec, for confirmations and LLM facts."""
    if not spec:
        return "nothing"
    bits = [spec.get("title") or "?"]
    for key, prefix in (("format", ""), ("language", ""), ("city", "in ")):
        if spec.get(key):
            bits.append(prefix + str(spec[key]).title() if prefix else str(spec[key]))
    if spec.get("venues"):
        bits.append("at " + ", ".join(v.title() for v in spec["venues"]))
    if spec.get("dates"):
        bits.append("on " + ", ".join(spec["dates"]))
    if spec.get("time_from"):
        bits.append("%s-%s" % (spec["time_from"], spec.get("time_to", "")))
    return " · ".join(b for b in bits if b)


def pretty_date(date_code):
    """'20260808' -> 'Saturday, 8 Aug'."""
    d = dt.datetime.strptime(date_code, "%Y%m%d")
    return "%s, %d %s" % (d.strftime("%A"), d.day, d.strftime("%b"))


def format_days(by_date):
    """One block per date, grouped by cinema, times on one line. No repetition."""
    lines = []
    for date_code in sorted(by_date):
        shows = by_date[date_code]
        if not shows:
            continue
        lines.append("\n📅 %s" % pretty_date(date_code))
        for venue in sorted({s["venue"] for s in shows}):
            at = sorted([s for s in shows if s["venue"] == venue], key=lambda s: s["mins"])
            times = ", ".join(s["time"] + (" ❌" if s["sold"] else "") for s in at)
            available = [s for s in at if not s["sold"]]
            prices = {s["price"] for s in available if s["price"]}
            lines.append("  🎬 %s" % short_venue(venue))
            lines.append("     🕒 %s" % times)
            if prices:
                lines.append("     💰 from ₹%s"
                             % min(prices, key=lambda p: float(p or 0)).split(".")[0])
        if SOURCE == "district":
            lines.append("  🔗 %s?fromdate=%s-%s-%s"
                         % (DISTRICT_URL, date_code[:4], date_code[4:6], date_code[6:8]))
        else:
            lines.append("  🔗 https://in.bookmyshow.com/movies/%s/buytickets/%s/%s"
                         % (MOVIE_SLUG, EVENT_CODE, date_code))
    return lines


def alert_text(by_date):
    """THE real alert. test_run() renders this verbatim so you see it in advance."""
    n = sum(len(v) for v in by_date.values())
    return "\n".join([
        "🚨🕷️ IT'S LIVE! %s %s TICKETS ARE OPEN! 🕷️🚨" % (LANGUAGE.upper(), FORMAT),
        "",
        "🍿 %s" % MOVIE_NAME,
        "🎟️ %d show%s between %s and %s" % (n, "" if n == 1 else "s", TIME_FROM, TIME_TO),
        "⚡ GO BOOK NOW - 4DX sells out fast!",
    ] + format_days(by_date) + ["", "❌ = already sold out"])


def shift_report(t):
    """End-of-shift summary: proof the bot is alive and what it saw.

    `t["watches"]` is {watch_id: {"title", "found": {date_code: count}}} -
    built fresh each cycle in run_cycle(), one entry per movie active during
    the shift; several can be listed here, not just one.
    """
    name = t["name"]
    lo, hi = next(((a, b) for n, a, b in SHIFTS if n == name), (0, 0))
    nxt = SHIFTS[(([s[0] for s in SHIFTS].index(name)) + 1) % len(SHIFTS)]

    lines = ["📋 %s SHIFT REPORT" % name.upper(),
             "🕒 %02d:00-%02d:00 IST · %s" % (lo, hi, pretty_date(t["date"])),
             "",
             "🔁 Checks run: %d%s" % (t["checks"],
                                      " (%s → %s)" % (t["first"], t["last"]) if t["checks"] else ""),
             "📡 %s reachable: %s" % ("District" if SOURCE == "district" else "BookMyShow",
                                      "yes ✅" if not t["errors"]
                                      else "%d failed check(s) ⚠️" % t["errors"])]
    if t.get("bookable"):
        lines.append("📆 Tickets on sale up to: %s" % pretty_date(t["bookable"]))

    lines.append("")
    watches = t.get("watches") or {}
    if not watches:
        lines.append("😴 No movie being watched right now.")
    else:
        all_found = True
        for w in watches.values():
            lines.append("🎯 Watching: %s" % w["title"])
            found = w.get("found") or {}
            for date_code, n in found.items():
                lines.append("  %s %s - %s" % ("✅" if n else "🔒", pretty_date(date_code),
                                               "%d shows FOUND, alert sent!" % n if n
                                               else "no matching shows scheduled yet"))
            if not (found and all(found.values())):
                all_found = False
        if all_found:
            lines += ["", "🎉 Everything's open. My job here is done!"]
    lines += ["", "⏭️ Next up: %s shift (%02d:00-%02d:00). Still watching. 👀"
              % (nxt[0], nxt[1], nxt[2])]
    return "\n".join(lines)


def live_report(hits, broken, bookable, checks=1):
    """A shift report describing this very moment, from an already-done scan.

    `hits` is {watch_id: {date_code: [(key, show), ...]}} - see
    movies/runner.py::run_cycle(). Short-circuits when nothing is being
    watched, rather than showing stale .env-configured field values as if
    they were a live default watch.
    """
    from . import watchspec
    watches = watchspec.load_all()
    if not watches:
        return ("😴 No movie is being watched right now. Tell me what to "
                "watch, e.g. \"%s\"." % example_watch_phrase())

    now = dt.datetime.now(IST)
    shift = shift_at(now)
    name = shift[0] if shift else SHIFTS[-1][0]   # 00:00-07:00: report the shift just ended
    stamp = now.strftime("%I:%M %p").lstrip("0")

    tally_watches = {}
    for w in watches:
        wid = w["id"]
        wh = (hits or {}).get(wid, {})
        tally_watches[wid] = {
            "title": w.get("title"),
            "found": {dc.replace("-", ""): len(wh.get(dc.replace("-", ""), []))
                     for dc in w.get("dates", [])},
        }
    return shift_report({
        "name": name, "date": now.strftime("%Y%m%d"), "checks": checks,
        "first": stamp, "last": stamp, "errors": len(broken), "bookable": bookable,
        "watches": tally_watches,
    })
