# Movie Notification Bot 🕷️

Watches BookMyShow and pings Telegram the moment tickets open for a specific
movie, on specific dates, in a specific format — then goes quiet again.

Current target: **Spider-Man: Brand New Day**, Hyderabad, **English 4DX 3D**,
**8–9 Aug 2026**, shows starting **06:00–20:00**.

## Why the old version was blocked

It scraped the BookMyShow website HTML. That page returns **403 to any
non-browser client** — verified, even from a residential IP with a real Chrome
user-agent. Header spoofing cannot fix it: the page expects a real browser to
run a JS challenge and present a browser TLS fingerprint, and Python can fake
neither. The parsed markup (`li.list-item`, `a.__name`) had also stopped
existing when BMS became a single-page app.

Two more faults meant the old workflow never even reached BMS:
`requirements.txt` listed the stdlib modules `time` and `datetime`, so
`pip install` failed, and the workflow invoked `demon_slayer_bot.py`, which
did not exist.

## How this version works

It calls the **same JSON API the BookMyShow Android app uses**, which answers a
well-formed app request normally. It is one request per date instead of a full
page render. On top of that:

- a stable device id (`x-bms-id`) rather than a new identity every run
- 0–45 s startup jitter, 4–11 s between dates
- 3 retries with backoff, session reuse, and a second known endpoint as fallback
- polling every 10 minutes, not every minute

Personal, low-rate, read-only use. It watches; it never books, holds or buys.

## Two traps this bot avoids

**1. BMS does not 404 a date that isn't open yet — it silently returns *today's*
showtimes instead.** A naive watcher sees a full venue list for "8 Aug", fires
"BOOKINGS OPEN!", and is wrong. Every response is checked:

```python
if str(details[0].get("Date")) != date_code:   # served a different day = not open
    return []
```

**2. The parent movie code does not expose every format.** Querying
`ET00447840` returns only English 2D — the 4DX 3D shows are invisible from it.
Each format is its own event, so the bot watches **`ET00502630`** (English
4DX 3D) directly. Verified on 5 Aug: the parent reported 36 English 2D shows
and zero 4DX, while the child reported 4DX 3D at three PVR screens.

## Silence is trustworthy, failure is loud

"Couldn't reach BMS" and "not open yet" look identical from outside, and the
first one silently loses you the tickets. So an unreachable BMS **exits
non-zero**. If the bot is quiet, it genuinely means "not open yet".

## Where it runs

**Locally, via Windows Task Scheduler — not GitHub Actions.**

BMS returns 403 to GitHub's runners. Verified twice on two different runners:
both endpoints, every retry, instant 403, while the identical request returns
200 from a home connection. Actions runs on Azure datacenter IPs and BMS treats
datacenter traffic differently. It is a network-level block, so no header or
endpoint change avoids it.

The workflow file is kept with its cron commented out and `workflow_dispatch`
still enabled, purely so the block can be re-probed later.

## Layout

```
bms_watch.py            CLI entry only
watcher/
  config.py             settings, headers, shift windows, keywords
  bms.py                fetch, parse, filter, scan
  messages.py           alerts, shift reports, formatting
  shifts.py             shift boundary handling
  state.py              seen.json
  telegram.py           send, poll, reply
  runner.py             the run modes
tests/test_watcher.py   offline self-checks, no framework
```

Only dependency is `requests`. There is no web server and no framework — long
polling means the bot makes outbound calls only, so there is nothing to host and
no inbound surface. A webhook would be the alternative, and would need a public
HTTPS host.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your own values

python bms_watch.py --selftest   # offline logic check, no network
python bms_watch.py --test       # test message + a preview of the real alert
python bms_watch.py --report     # status report right now
python bms_watch.py              # one check, for cron / Task Scheduler
python -u bms_watch.py --serve   # stay running: instant chat replies
```

**`--serve` runs two clocks on purpose.** Telegram is long-polled, so a message
you type is answered in about a second. BookMyShow is still only scanned every
`SCAN_EVERY` seconds (600 by default) — replying fast must not mean hammering
them — and a reply between scans reports the last scan's data. Shift boundaries
are checked every loop, so a shift report lands on the boundary itself.

Use `-u` when redirecting its output: Python buffers stdout, so a long-running
process otherwise writes nothing to its log until it exits.

Scheduled run every 10 minutes (Windows):

```powershell
Get-ScheduledTaskInfo   -TaskName "BMS Spiderman 4DX Watcher"   # last/next run
Start-ScheduledTask     -TaskName "BMS Spiderman 4DX Watcher"   # force a check
Disable-ScheduledTask   -TaskName "BMS Spiderman 4DX Watcher"   # pause
Unregister-ScheduledTask -TaskName "BMS Spiderman 4DX Watcher"  # remove
```

## Messages you'll get

**The alert** — once, when tickets open:

```
🚨🕷️ IT'S LIVE! ENGLISH 4DX 3D TICKETS ARE OPEN! 🕷️🚨

🍿 Spider-Man: Brand New Day
🎟️ 12 shows between 06:00 and 20:00
⚡ GO BOOK NOW - 4DX sells out fast!

📅 Saturday, 8 Aug
  🎬 PVR Superplex Inorbit, Cyberabad
     🕒 10:25 AM, 01:25 PM, 04:30 PM, 08:00 PM
     💰 from ₹350
  🔗 https://in.bookmyshow.com/movies/...
```

**Shift reports** — automatic, at the end of each IST shift:

| Shift | Window |
|---|---|
| Morning | 07:00–12:00 |
| Afternoon | 12:00–18:00 |
| Evening | 18:00–21:00 |
| Night | 21:00–24:00 |

Each gives checks run, first/last check time, whether BMS was reachable, how far
ahead BMS is currently selling, and per-date status. Watch the
`📆 BMS is selling up to` line — when it reaches 8 Aug, you're hours away.

A report is sent by the first run of the *next* shift, so **a missing report
means the watcher stopped**. Nothing is reported between 00:00–07:00, though the
watcher still runs and would still alert.

Shifts are computed in IST via a fixed `+05:30` offset — India has no DST, so
this is exact and needs no tzdata on a UTC host.

**On demand** — type into the bot's chat:

> report · status · check · update · news

with or without a `/`, in any sentence (`any update?` works). There is no LLM
here, just keyword matching. Under `--serve` the reply is effectively instant;
under a one-shot schedule it arrives on the next run.

## Config

| Var | Default | Meaning |
|---|---|---|
| `EVENT_CODE` | `ET00502630` | BMS code **for the format**, not the parent movie |
| `REGION_CODE` | `HYD` | City |
| `DATES` | `20260808,20260809` | Dates to watch, `YYYYMMDD` |
| `TIME_FROM` / `TIME_TO` | `06:00` / `20:00` | Show start window |
| `FORMAT` | `4DX 3D` | Matched with punctuation stripped; blank = any |
| `LANGUAGE` | `English` | Blank = any |
| `VENUES` | *(empty)* | Comma-separated substrings; empty = all |
| `MOVIE_SLUG` / `MOVIE_NAME` | Spider-Man | Booking link and alert title only |
| `SCAN_EVERY` | `600` | `--serve` only: seconds between BMS scans |
| `LONG_POLL` | `25` | `--serve` only: seconds each Telegram poll is held open |

Every setting has a default in code; `.env` and secrets only override.

## Disclaimer

Personal, non-commercial use. Not affiliated with BookMyShow.

---

<div align="center">

**Built with 💻 and ☕ by Vishal Goud**

[![LinkedIn](https://img.shields.io/badge/LinkedIn-blue?style=flat&logo=linkedin)](http://www.linkedin.com/in/vishalgoud3105)
[![GitHub](https://img.shields.io/badge/GitHub-black?style=flat&logo=github)](https://github.com/Vishalgoud3105)
[![Portfolio](https://img.shields.io/badge/Portfolio-orange?style=flat)](https://vishalgoud3105.github.io/Portfolio/)

</div>
