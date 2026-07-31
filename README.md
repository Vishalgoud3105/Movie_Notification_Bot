# Movie Notification Bot 🕷️

Watches BookMyShow and pings Telegram the moment tickets open for a specific
movie, on specific dates, in a specific format — then shuts up again.

Current target: **Spider-Man: Brand New Day**, Hyderabad, **8–9 Aug 2026**,
**English 4DX 3D**, shows starting **06:00–20:00**.

## Why it doesn't get blocked

The old version scraped the BMS website HTML. That page returns **403 to any
non-browser client** — verified, even from a residential IP with a real Chrome
user-agent. No amount of header spoofing fixes it, because the block is on the
request pattern, not the header string.

This version talks to the **same JSON API the BookMyShow Android app uses**
(`/api/movies-data/showtimes-by-event`). It answers a well-formed app request
normally. On top of that:

- one request per date instead of a full page render
- a stable device id (`x-bms-id`) instead of a fresh identity every run
- 0–45 s startup jitter so runs don't land on the exact cron tick
- 4–11 s gap between dates
- 3 retries with backoff, session reuse (keep-alive)
- 10-minute polling, not 1-minute hammering

Personal, low-rate, read-only use. Don't crank the schedule.

## The trap this bot avoids

**BMS does not 404 a date that isn't open yet — it silently returns *today's*
showtimes instead.** A naive watcher sees a full venue list for "Aug 8", fires
"BOOKINGS OPEN!", and is wrong. So every response is checked:

```python
if str(details[0].get("Date")) != date_code:   # served a different day = not open
    return []
```

`ShowDatesArray[].isDisabled` mirrors the greyed-out dates you see on the site,
and is logged each run so you can watch the booking window creep toward your date.

## Failure is loud, silence is trustworthy

"Couldn't reach BMS" and "not open yet" look identical from outside, and the
first one silently loses you the tickets. So an unreachable BMS **exits
non-zero** — GitHub marks the run failed and emails you. If the bot is quiet,
it genuinely means "not open yet".

## Setup

1. `pip install -r requirements.txt`
2. `cp .env.example .env`, fill in your Telegram token + chat id (`.env` is gitignored)
3. `python bms_watch.py --selftest` — offline logic check
4. `python bms_watch.py` — one real run

For GitHub Actions, add `TELEGRAM_API_TOKEN` and `TELEGRAM_CHAT_ID` under
**Settings → Secrets and variables → Actions**. Everything else lives in
`.github/workflows/schedule.yml`.

> ⚠️ **Scheduled workflows only run on the repository's default branch.** On any
> other branch the workflow exists but never fires. Merge to the default branch
> before you rely on it.

Notification state (`seen.json`) rides in the Actions cache, so you get told
once, not every 10 minutes.

## Config

| Var | Default | Meaning |
|---|---|---|
| `EVENT_CODE` | `ET00447840` | BMS movie code (parent — covers all formats) |
| `REGION_CODE` | `HYD` | City |
| `DATES` | `20260808,20260809` | Dates to watch, `YYYYMMDD` |
| `TIME_FROM` / `TIME_TO` | `06:00` / `20:00` | Show start window |
| `FORMAT` | `4DX` | Substring match; blank = any format |
| `LANGUAGE` | `English` | Substring match; blank = any language |
| `VENUES` | *(empty)* | Comma-separated substrings; empty = all venues |

Finding another movie's code: `ET00…` appears in its BMS URL. The 4DX variant is
its own child event (`ET00502630` here), but you query the **parent** code — the
response carries every format, and the filter picks 4DX out.

## Disclaimer

Personal, non-commercial use. Not affiliated with BookMyShow.

---

<div align="center">

**Built with 💻 and ☕ by Vishal Goud**

[![LinkedIn](https://img.shields.io/badge/LinkedIn-blue?style=flat&logo=linkedin)](http://www.linkedin.com/in/vishalgoud3105)
[![GitHub](https://img.shields.io/badge/GitHub-black?style=flat&logo=github)](https://github.com/Vishalgoud3105)
[![Portfolio](https://img.shields.io/badge/Portfolio-orange?style=flat)](https://vishalgoud3105.github.io/Portfolio/)

</div>
