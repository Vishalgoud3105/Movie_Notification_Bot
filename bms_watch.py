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

API = "https://in.bookmyshow.com/api/movies-data/showtimes-by-event"

EVENT_CODE = os.environ.get("EVENT_CODE", "ET00447840")
REGION_CODE = os.environ.get("REGION_CODE", "HYD")
DATES = [d.strip() for d in os.environ.get("DATES", "20260808,20260809").split(",") if d.strip()]
TIME_FROM = os.environ.get("TIME_FROM", "06:00")
TIME_TO = os.environ.get("TIME_TO", "20:00")
VENUES = [v.strip().lower() for v in os.environ.get("VENUES", "").split(",") if v.strip()]
# 4DX has no child-event of its own yet for this movie, so match it in either
# the child-event dimension or the venue's show Attributes. Blank = any.
FORMAT = os.environ.get("FORMAT", "4DX").strip().upper()
LANGUAGE = os.environ.get("LANGUAGE", "English").strip().upper()
MOVIE_SLUG = os.environ.get("MOVIE_SLUG", "spiderman-brand-new-day")
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
        try:
            r = session.get(API, params=params, headers=HEADERS, timeout=25)
            if r.status_code == 200:
                return r.json()
            print("HTTP %s for %s (attempt %d)" % (r.status_code, date_code, attempt + 1))
        except (requests.RequestException, ValueError) as e:
            print("request error for %s: %s" % (date_code, e))
        time.sleep(5 * (attempt + 1) + random.uniform(0, 4))
    return None


def shows_for(data, date_code):
    """Flatten the API payload to [(key, text)] for shows inside the time window.

    Empty until bookings for date_code actually open. BMS does NOT 404 an
    unopened date - it quietly serves today's shows instead - so the Date in
    the response must be checked or every run reports a false opening.
    """
    lo, hi = to_minutes(TIME_FROM), to_minutes(TIME_TO)
    details = data.get("ShowDetails") or []
    if not details:
        return []
    if str(details[0].get("Date")) != date_code:
        print("%s not open yet (API served %s)" % (date_code, details[0].get("Date")))
        return []

    event = details[0].get("Event") or {}
    children = event.get("ChildEvents") if isinstance(event, dict) else event
    # showtimes carry their own EventCode -> language/format lives on the child event
    variants = {c["EventCode"]: "%s %s" % (c.get("EventLang", ""), c.get("EventDimension", ""))
                for c in (children or [])}

    out = []
    for venue in details[0].get("Venues") or []:
        name = venue.get("VenueName", "?")
        if VENUES and not any(v in name.lower() for v in VENUES):
            continue
        for show in venue.get("ShowTimes") or []:
            when = show.get("ShowTime", "")
            try:
                mins = to_minutes(when)
            except ValueError:
                continue
            if not lo <= mins <= hi:
                continue
            variant = variants.get(show.get("EventCode"), "").strip()
            attrs = (show.get("Attributes") or "").strip()
            label = ("%s %s" % (variant, attrs)).upper()
            if FORMAT and FORMAT not in label:
                continue
            if LANGUAGE and LANGUAGE not in label:
                continue
            key = "|".join([date_code, name, when, show.get("EventCode", ""), attrs])
            sold_out = show.get("Availability") == "S"
            out.append((key, "%s - %s%s%s" % (
                when, name,
                " [%s]" % " / ".join(x for x in (variant, attrs) if x) if (variant or attrs) else "",
                " (SOLD OUT)" if sold_out else "")))
    return out


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


def main():
    seen = set()
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            seen = set(json.load(f))
    # Cron fires on the exact tick for everyone; a random offset keeps this
    # request out of the top-of-the-minute crowd.
    time.sleep(random.uniform(0, 45))

    session = requests.Session()
    fresh, all_keys, broken = [], set(), []
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
        found = shows_for(data, date_code)
        print("%s: %d shows in window" % (date_code, len(found)))
        for key, text in sorted(found):
            all_keys.add(key)
            if key not in seen:
                fresh.append((date_code, text))

    if fresh:
        lines = ["BOOKINGS OPEN - shows between %s and %s:" % (TIME_FROM, TIME_TO)]
        for date_code in DATES:
            day = [t for d, t in fresh if d == date_code]
            if day:
                lines.append("\n%s:" % dt.datetime.strptime(date_code, "%Y%m%d").strftime("%a %d %b"))
                lines.extend("  " + t for t in day)
                lines.append("  book: https://in.bookmyshow.com/movies/%s/buytickets/%s/%s"
                             % (MOVIE_SLUG, EVENT_CODE, date_code))
        send_telegram("\n".join(lines))
    else:
        print("nothing new")

    with open(STATE_FILE, "w") as f:
        json.dump(sorted(all_keys | seen), f)

    if broken:
        # Loud on purpose. "Couldn't reach BMS" looks exactly like "not open yet"
        # from the outside, and silently watching nothing is the one failure that
        # loses the tickets. Non-zero exit -> GitHub marks the run failed and mails you.
        sys.exit("could not reach BMS for %s - watcher is NOT working" % ", ".join(broken))


def demo():
    """Self-check for the real logic: date guard, time window, format filter."""
    global FORMAT, LANGUAGE, VENUES
    VENUES = []
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
    texts = [t for _, t in shows_for(payload, "20260808")]
    assert sorted(texts) == sorted([
        "10:00 AM - PVR Nexus Mall [English 4DX 3D]",
        "07:10 PM - PVR Nexus Mall [English 4DX 3D] (SOLD OUT)",
        "09:00 AM - AMB Cinemas [English 3D / ENGLISH 4DX]",
    ]), texts       # 11:30 PM out of window, plain 3D and Telugu 4DX filtered out

    FORMAT, LANGUAGE = "", ""       # blank filters = report everything
    assert len(shows_for(payload, "20260808")) == 5
    print("self-check ok")


if __name__ == "__main__":
    demo() if "--selftest" in sys.argv else main()
