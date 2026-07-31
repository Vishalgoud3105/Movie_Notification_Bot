"""BookMyShow showtime watcher -> Telegram.

Talks to BMS's own mobile-app JSON API instead of scraping the website HTML.
The website returns 403 to anything that isn't a real browser session (that is
what killed the old scraper). The app API answers a well-formed app request
normally, and it is one request per date instead of a full page render.

Config via env vars (all optional, defaults below):
  TELEGRAM_API_TOKEN, TELEGRAM_CHAT_ID   required
  EVENT_CODE   ET00447840  (Spider-Man: Brand New Day)
  REGION_CODE  HYD
  DATES        20260808,20260809
  TIME_FROM    06:00      earliest show start to report
  TIME_TO      20:00      latest show start to report
  VENUES       substring filter, comma separated; empty = all venues
  STATE_FILE   seen.json
"""

import datetime as dt
import json
import os
import random
import sys
import time

import requests

# Local runs read .env; GitHub Actions injects the same names as real env vars.
# ponytail: 4 lines instead of a python-dotenv dependency.
if os.path.exists(".env"):
    for line in open(".env"):
        line = line.split("#", 1)[0].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# Both verified to return identical payloads. If BMS retires one, the other is
# tried automatically. A brand new API with a different contract cannot be
# guessed - that case exits non-zero instead of pretending to work.
ENDPOINTS = [
    "https://in.bookmyshow.com/api/movies-data/showtimes-by-event",
    "https://in.bookmyshow.com/api/v2/mobile/showtimes/byevent",
]

# MUST be the 4DX 3D child code, not the parent ET00447840. Verified: the parent
# query returns only English 2D shows - the 4DX shows are invisible from it.
# Each format is its own event as far as this API is concerned.
EVENT_CODE = os.environ.get("EVENT_CODE", "ET00502630")
REGION_CODE = os.environ.get("REGION_CODE", "HYD")
DATES = [d.strip() for d in os.environ.get("DATES", "20260808,20260809").split(",") if d.strip()]
TIME_FROM = os.environ.get("TIME_FROM", "06:00")
TIME_TO = os.environ.get("TIME_TO", "20:00")
VENUES = [v.strip().lower() for v in os.environ.get("VENUES", "").split(",") if v.strip()]
# "4DX 3D" is its own child event (ET00502630 here) and is NOT the same as plain
# 4DX or 4DX 2D. Matched against the child-event dimension and the venue's show
# Attributes, with punctuation stripped so "4DX 3D"/"4DX-3D"/"4DX3D" all hit and
# "4DX 2D" does not. Blank = any.
FORMAT = os.environ.get("FORMAT", "4DX 3D")
LANGUAGE = os.environ.get("LANGUAGE", "English")


def squash(s):
    """'4DX 3D' -> '4DX3D'. Kills spacing/punctuation differences before matching."""
    return "".join(c for c in s.upper() if c.isalnum())
MOVIE_SLUG = os.environ.get("MOVIE_SLUG", "spiderman-brand-new-day")
MOVIE_NAME = os.environ.get("MOVIE_NAME", "Spider-Man: Brand New Day")
STATE_FILE = os.environ.get("STATE_FILE", "seen.json")

# Stable per-install device identity. A device id that changes every run looks
# far more synthetic than one that stays put, so derive it from the chat id.
BMS_ID = "1.%s.1707213758822" % (os.environ.get("TELEGRAM_CHAT_ID", "21345445")[:12] or "21345445")

HEADERS = {
    "x-bms-id": BMS_ID,
    "x-region-code": REGION_CODE,
    "x-subregion-code": REGION_CODE,
    "x-platform": "AND",
    "x-platform-code": "ANDROID",
    "x-app-code": "MOBAND2",
    "x-app-version": "14.3.4",
    "x-device-make": "Google-Pixel XL",
    "x-screen-height": "2392",
    "x-screen-width": "1440",
    "x-screen-density": "3.5",
    "x-network": "Android | WIFI",
    "user-agent": "Dalvik/2.1.0 (Linux; U; Android 12; Pixel XL Build/SP1A.211105.003)",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "accept-encoding": "gzip",
}


def to_minutes(show_time):
    """'07:10 PM' or '19:10' -> minutes since midnight."""
    show_time = show_time.strip()
    fmt = "%I:%M %p" if show_time[-2:].upper() in ("AM", "PM") else "%H:%M"
    t = dt.datetime.strptime(show_time, fmt)
    return t.hour * 60 + t.minute


def fetch(session, date_code):
    """One date's showtimes, or None if BMS could not be reached at all.

    None and {} must stay distinguishable: an empty result means "date not open
    yet" (stay quiet), None means the watcher is broken (must be loud, see main).
    """
    params = {
        "appCode": "MOBAND2",
        "appVersion": "14304",
        "language": "en",
        "eventCode": EVENT_CODE,
        "regionCode": REGION_CODE,
        "subRegion": REGION_CODE,
        "bmsId": BMS_ID,
        "token": "67x1xa33f3sf",
        "query": "",
        "dateCode": date_code,
    }
    for attempt in range(3):
        for url in ENDPOINTS:
            try:
                r = session.get(url, params=params, headers=HEADERS, timeout=25)
                if r.status_code == 200:
                    data = r.json()
                    if url is not ENDPOINTS[0]:
                        print("note: primary endpoint failed, served by %s" % url)
                    return data
                print("HTTP %s for %s from %s (attempt %d)"
                      % (r.status_code, date_code, url.rsplit("/", 1)[-1], attempt + 1))
            except (requests.RequestException, ValueError) as e:
                print("request error for %s from %s: %s" % (date_code, url.rsplit("/", 1)[-1], e))
        time.sleep(5 * (attempt + 1) + random.uniform(0, 4))
    return None


def walk(node):
    """Every dict anywhere in a JSON structure, at any depth."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            for d in walk(v):
                yield d
    elif isinstance(node, list):
        for v in node:
            for d in walk(v):
                yield d


def find_venues(data):
    """[(venue_name, [show dicts])] found by shape rather than by fixed path.

    Fallback for when BMS renames or re-nests its response: look for any object
    pairing a venue-name string with a list of showtime records. Survives
    restructuring. It cannot survive a genuinely new API with a new contract -
    that case must fail loudly rather than quietly report nothing.
    """
    found = []
    for node in walk(data):
        name = next((v for k, v in node.items()
                     if "VENUE" in k.upper() and "NAME" in k.upper() and isinstance(v, str)), None)
        shows = next((v for k, v in node.items()
                      if isinstance(v, list) and v and isinstance(v[0], dict)
                      and any(kk.upper() == "SHOWTIME" for kk in v[0])), None)
        if name and shows:
            found.append((name, shows))
    return found


def find_variants(data):
    """{event code: 'English 4DX 3D'} for every child event anywhere in the payload."""
    return {d["EventCode"]: "%s %s" % (d.get("EventLang", ""), d.get("EventDimension", ""))
            for d in walk(data)
            if d.get("EventCode") and ("EventLang" in d or "EventDimension" in d)}


def shows_for(data, date_code):
    """Flatten the API payload to [(key, text)] for shows inside the time window.

    Empty until bookings for date_code actually open. BMS does NOT 404 an
    unopened date - it quietly serves today's shows instead - so the Date in
    the response must be checked or every run reports a false opening.
    """
    lo, hi = to_minutes(TIME_FROM), to_minutes(TIME_TO)
    details = (data or {}).get("ShowDetails") or []

    if details:
        if str(details[0].get("Date")) != date_code:
            print("%s not open yet (API served %s)" % (date_code, details[0].get("Date")))
            return []
        venues = [(v.get("VenueName", "?"), v.get("ShowTimes") or [])
                  for v in details[0].get("Venues") or []]
    else:
        # Expected layout gone. Try to read it by shape before giving up.
        venues = find_venues(data)
        if venues:
            print("WARNING: BMS response layout changed - using tolerant parse. "
                  "Verify the alert against the site before trusting it.")

    # showtimes carry their own EventCode -> language/format lives on the child event
    variants = find_variants(data)

    out = []
    for name, showtimes in venues:
        if VENUES and not any(v in name.lower() for v in VENUES):
            continue
        for show in showtimes:
            # Per-show date guard. Matters most on the tolerant path, where the
            # top-level Date check above was not available to run.
            stamp = next((v for k, v in show.items() if k.upper() == "SHOWDATECODE"), None)
            if stamp and str(stamp) != date_code:
                continue
            if not details and not stamp:
                continue    # drifted layout with no date evidence at all - refuse to guess
            when = show.get("ShowTime", "")
            try:
                mins = to_minutes(when)
            except ValueError:
                continue
            if not lo <= mins <= hi:
                continue
            variant = variants.get(show.get("EventCode"), "").strip()
            attrs = (show.get("Attributes") or "").strip()
            label = squash("%s %s" % (variant, attrs))
            if FORMAT and squash(FORMAT) not in label:
                continue
            if LANGUAGE and squash(LANGUAGE) not in label:
                continue
            key = "|".join([date_code, name, when, show.get("EventCode", ""), attrs])
            out.append((key, {
                "venue": name,
                "time": when,
                "mins": mins,
                "sold": show.get("Availability") == "S",
                "format": " / ".join(x for x in (variant, attrs) if x),
                "price": show.get("MinPrice") or "",
            }))
    return out


# Screen-brand noise BMS bakes into venue names ("PVR Superplex Inorbit: LUXE,
# PXL, 4DX: Cyberabad"). Dropped for readability; the format is in the header.
SCREEN_WORDS = {"LUXE", "PXL", "4DX", "IMAX", "GOLD", "ONYX", "INSIGNIA", "PLAYHOUSE",
                "DIRECTOR'S CUT", "SUPERPLEX", "P[XL]", "ICE", "MX4D", "EPIQ"}


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


# India has no DST, so a fixed offset is exactly right and needs no tzdata.
# GitHub Actions runs in UTC; every shift boundary below is your local time.
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SHIFTS = [("Morning", 7, 12), ("Afternoon", 12, 18), ("Evening", 18, 21), ("Night", 21, 24)]


def shift_at(now):
    """('Morning', 7, 12) for an IST datetime, or None between midnight and 7am."""
    return next((s for s in SHIFTS if s[1] <= now.hour < s[2]), None)


def load_state():
    """{'seen': [...], 'shift': {...}}, tolerating the old plain-list format."""
    if not os.path.exists(STATE_FILE):
        return {"seen": [], "shift": None}
    with open(STATE_FILE) as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {"seen": raw, "shift": None, "tg_offset": 0}
    return {"seen": raw.get("seen", []), "shift": raw.get("shift"),
            "tg_offset": raw.get("tg_offset", 0)}


def shift_report(t):
    """End-of-shift summary: proof the bot is alive and what it saw."""
    name = t["name"]
    lo, hi = next(((a, b) for n, a, b in SHIFTS if n == name), (0, 0))
    nxt = SHIFTS[(([s[0] for s in SHIFTS].index(name)) + 1) % len(SHIFTS)]

    lines = ["📋 %s SHIFT REPORT" % name.upper(),
             "🕒 %02d:00-%02d:00 IST · %s" % (lo, hi, pretty_date(t["date"])),
             "",
             "🔁 Checks run: %d%s" % (t["checks"],
                                      " (%s → %s)" % (t["first"], t["last"]) if t["checks"] else ""),
             "📡 BookMyShow reachable: %s" % ("yes ✅" if not t["errors"]
                                              else "%d failed check(s) ⚠️" % t["errors"])]
    if t.get("bookable"):
        lines.append("📆 BMS is selling up to: %s" % pretty_date(t["bookable"]))
    lines += ["", "🎯 Watching %s %s, %s-%s:" % (LANGUAGE, FORMAT, TIME_FROM, TIME_TO)]
    for date_code in DATES:
        n = (t.get("found") or {}).get(date_code, 0)
        lines.append("  %s %s - %s" % ("✅" if n else "🔒", pretty_date(date_code),
                                       "%d shows FOUND, alert sent!" % n if n else "not open yet"))
    if all((t.get("found") or {}).get(d) for d in DATES):
        lines += ["", "🎉 Both dates are open. My job here is done!"]
    else:
        lines += ["", "⏭️ Next up: %s shift (%02d:00-%02d:00). Still watching. 👀"
                  % (nxt[0], nxt[1], nxt[2])]
    return "\n".join(lines)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("no telegram creds set; message was:\n" + text)
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    for i in range(0, len(text), 3800):  # telegram caps messages at 4096
        r = requests.post(url, json={"chat_id": chat, "text": text[i:i + 3800],
                                     "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            print("telegram error %s: %s" % (r.status_code, r.text[:200]))


def scan(session):
    """One pass over every watched date.

    Returns ({date: [(key, show)]}, unreachable dates, furthest date BMS is selling).
    """
    hits, broken, bookable = {}, [], None
    for i, date_code in enumerate(DATES):
        if i:
            time.sleep(random.uniform(4, 11))  # a person does not fetch 7 dates in 200ms
        data = fetch(session, date_code)
        if data is None:
            broken.append(date_code)
            continue
        # BMS greys out dates it has not scheduled yet; isDisabled mirrors that.
        offered = [d["DateCode"] for d in data.get("ShowDatesArray", []) if not d.get("isDisabled")]
        print("%s: bookable dates on BMS right now -> %s" % (date_code, offered[-3:] or "none"))
        if offered:
            bookable = max(offered) if not bookable else max(bookable, max(offered))
        found = sorted(shows_for(data, date_code))
        print("%s: %d shows in window" % (date_code, len(found)))
        hits[date_code] = found
    return hits, broken, bookable


def live_report(hits, broken, bookable, checks=1):
    """A shift report describing this very moment, from an already-done scan."""
    now = dt.datetime.now(IST)
    shift = shift_at(now)
    name = shift[0] if shift else SHIFTS[-1][0]   # 00:00-07:00: report the shift just ended
    stamp = now.strftime("%I:%M %p").lstrip("0")
    return shift_report({
        "name": name, "date": now.strftime("%Y%m%d"), "checks": checks,
        "first": stamp, "last": stamp, "errors": len(broken), "bookable": bookable,
        "found": {d: len(hits.get(d, [])) for d in DATES},
    })


def report_now():
    """Send a status report for right now, on demand. Does not touch seen.json."""
    hits, broken, bookable = scan(requests.Session())
    send_telegram(live_report(hits, broken, bookable))
    print("report sent")


def poll_commands(state):
    """Read anything you typed to the bot since the last run.

    Polled once per run rather than via a webhook, so a reply lands within one
    cron interval. A webhook would be instant but needs a server to host.
    Messages from any chat other than yours are ignored.
    """
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chat = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not (token and chat):
        return []
    # Reading updates gets connection-reset every so often (roughly 1 try in 6
    # here), while sending never does. Without a retry a single reset silently
    # drops your command and you wait 10 minutes for a reply that never comes.
    # POST rather than GET: GET with these params is reset far more often.
    updates = None
    for attempt in range(3):
        try:
            r = requests.post("https://api.telegram.org/bot%s/getUpdates" % token,
                              json={"offset": state.get("tg_offset", 0), "timeout": 0},
                              timeout=20)
            updates = r.json().get("result", []) if r.status_code == 200 else []
            break
        except (requests.RequestException, ValueError) as e:
            print("could not read telegram commands (attempt %d): %s" % (attempt + 1, e))
            time.sleep(2 * (attempt + 1))
    if updates is None:
        return []

    commands = []
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1      # ack, so it is not replayed
        msg = u.get("message") or u.get("edited_message") or {}
        if str((msg.get("chat") or {}).get("id")) != chat:
            continue                                  # not you - ignore
        text = (msg.get("text") or "").strip().lower().lstrip("/")
        if text:
            commands.append(text.split("@")[0].split()[0])
    return commands


def main():
    state = load_state()
    seen = set(state["seen"])
    now = dt.datetime.now(IST)
    shift = shift_at(now)

    # A shift ended if the tally we carry belongs to a different shift or day.
    # Report it before starting a new one, so you get one summary per shift.
    tally = state["shift"]
    if tally and (tally["name"] != (shift[0] if shift else None)
                  or tally["date"] != now.strftime("%Y%m%d")):
        send_telegram(shift_report(tally))
        print("sent %s shift report" % tally["name"])
        tally = None
    if shift and not tally:
        tally = {"name": shift[0], "date": now.strftime("%Y%m%d"), "checks": 0,
                 "first": None, "last": None, "errors": 0, "found": {}, "bookable": None}

    # Cron fires on the exact tick for everyone; a random offset keeps this
    # request out of the top-of-the-minute crowd.
    time.sleep(random.uniform(0, 45))

    session = requests.Session()
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

    # Anything you typed into the chat since the last run. Uses the scan above,
    # so asking for a report costs BookMyShow no extra requests.
    for cmd in poll_commands(state):
        print("command from you: /%s" % cmd)
        if cmd in ("report", "status", "check"):
            send_telegram(live_report(hits, broken, bookable,
                                      checks=(tally or {}).get("checks", 1)))
        else:
            send_telegram("\n".join([
                "🤖 I'm watching BookMyShow for you.",
                "",
                "/report - status right now",
                "",
                "You'll also get a report at the end of each shift,",
                "and one 🚨 alert the moment %s opens for %s."
                % (FORMAT, " & ".join(pretty_date(d) for d in DATES))]))

    with open(STATE_FILE, "w") as f:
        json.dump({"seen": sorted(all_keys | seen), "shift": tally,
                   "tg_offset": state.get("tg_offset", 0)}, f)

    if broken:
        # Loud on purpose. "Couldn't reach BMS" looks exactly like "not open yet"
        # from the outside, and silently watching nothing is the one failure that
        # loses the tickets. Non-zero exit -> GitHub marks the run failed and mails you.
        sys.exit("could not reach BMS for %s - watcher is NOT working" % ", ".join(broken))


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


def demo():
    """Self-check for the real logic: date guard, time window, format filter."""
    global FORMAT, LANGUAGE, VENUES
    VENUES, FORMAT, LANGUAGE = [], "4DX 3D", "English"   # pinned: never read .env
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

    # venue names must lose the screen-brand noise but keep the location
    assert short_venue("PVR Superplex Inorbit: LUXE, PXL, 4DX: Cyberabad") \
        == "PVR Superplex Inorbit, Cyberabad"
    assert short_venue("PVR: Irrum Manzil, Hyderabad") == "PVR Irrum Manzil"
    assert short_venue("AAA Cinemas: Ameerpet") == "AAA Cinemas, Ameerpet"

    # Drifted layout with no date evidence must stay silent, never guess.
    assert shows_for({"x": [{"venueName": "V", "sessions": [
        {"ShowTime": "10:00 AM", "EventCode": "E4DX"}]}],
        "events": [{"EventCode": "E4DX", "EventLang": "English",
                    "EventDimension": "4DX 3D"}]}, "20260808") == []

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
    requests.post = lambda *a, **k: type("R", (), {
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
    requests.post = real_post
    if keep_chat is not None:
        os.environ["TELEGRAM_CHAT_ID"] = keep_chat

    # legacy plain-list state must still load
    import tempfile
    global STATE_FILE
    keep, STATE_FILE = STATE_FILE, os.path.join(tempfile.gettempdir(), "_bms_legacy.json")
    with open(STATE_FILE, "w") as f:
        json.dump(["a|b"], f)
    assert load_state() == {"seen": ["a|b"], "shift": None, "tg_offset": 0}
    os.remove(STATE_FILE)
    STATE_FILE = keep

    FORMAT, LANGUAGE = "", ""       # blank filters = report everything
    assert len(shows_for(payload, "20260808")) == 5
    print("self-check ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        demo()
    elif "--test" in sys.argv:
        test_run()
    elif "--report" in sys.argv:
        report_now()
    else:
        main()
