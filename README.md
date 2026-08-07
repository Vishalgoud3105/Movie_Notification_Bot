# Movie Notification Bot 🕷️

Watches a ticketing site and pings Telegram the moment shows appear for a
specific movie, on specific dates, in a specific format — then goes quiet again.

Tell it what to watch in plain English, or set it in `.env`. It never books
anything; it only watches.

> Built to catch **Spider-Man: Brand New Day** in **English 4DX 3D** at
> **PVR Irrum Manzil, Hyderabad**. It worked — the alert fired and the tickets
> were booked.

## Two things that make it trustworthy

**1. Silence means "not open yet", never "I'm broken."** An unreachable site
exits non-zero, and end-of-shift reports arrive whether or not anything was
found — so a *missing* report is itself the alarm.

**2. The LLM is never asked whether shows exist.** It interprets your words and
phrases replies. Detection is always parsed site data. A model that confidently
says "not open yet" is indistinguishable from a correct one right up until the
tickets are gone.

## Why it reads District, not BookMyShow

BookMyShow returns **403 to every datacenter IP**. Verified on GitHub Actions
(Azure) and on Oracle Cloud in `ap-hyderabad-1` — both endpoints, every retry —
while identical code gets 200 from a home connection. Being in-country didn't
help; it's a hosting-ASN block.

Two nights went into changing *where* the request came from. The fix was
changing *who we asked*: **District (district.in)** sells the same PVR shows and
serves datacenter IPs fine.

District is also the better source:

| | BookMyShow | District |
|---|---|---|
| Format | hidden behind a separate child event code | explicit `scrnFmt: "4DX-3D"` |
| Unopened dates | silently returns *today's* shows | echoes `searchDate` honestly |
| Seats | sold-out flag | real counts (`avail`/`total`) |
| From a server | ❌ 403 | ✅ 200 |

`SOURCE=bms` still works from a residential connection.

## Layout

```
watch.py                CLI entry only
watcher/
  config.py             settings, shift windows, keywords
  district.py           the live source: fetch + parse district.in
  bms.py                the BookMyShow reader (SOURCE=bms, home only)
  sources.py            scan() — picks a source, one pass over the dates
  messages.py           alerts, shift reports, formatting
  shifts.py             shift boundary handling
  state.py              seen.json
  telegram.py           send, poll, reply
  runner.py             the run modes
  llm.py                Groq client
  prompt_template.py    extraction / chat / troubleshooting prompts
  brain.py              routes a message: keywords → LLM → fallback
  watchspec.py          the live watch; set by chat, reset when the goal fires
  search.py             resolves a title against District's real catalogue
tests/test_watcher.py   offline self-checks, no framework
deploy/watcher.service  systemd unit
```

Only dependency is **`requests`** — Groq speaks the OpenAI chat shape, so no SDK.
No web server, no framework: long polling means outbound calls only, so there's
nothing to host and no inbound surface.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your own values

python watch.py --selftest    # offline logic checks, no network
python watch.py --report      # status report right now
python watch.py --test        # test message + preview of the real alert
python watch.py               # one check, for cron
python -u watch.py --serve    # stay running: instant chat replies
```

Use `-u` when redirecting output — Python buffers stdout, so a long-running
process otherwise logs nothing until it exits.

### Deploy (Linux, systemd)

```bash
sudo cp deploy/watcher.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now watcher
journalctl -u watcher -f
```

`Restart=always`, starts at boot. Runs comfortably on a 1 GB Always Free VM
(~40 MB, near-zero CPU) — add swap, since 1 GB with none is fragile.

**Never run two watchers on one bot token.** Telegram allows a single
`getUpdates` consumer; two fight over messages and can double-alert.

## Talking to it

Needs `GROQ_API_KEY` (free at console.groq.com). Describe what you want:

> *"watch Spider-Man 4DX 3D at Irrum Manzil on 8 and 9 Aug, morning to evening"*

It resolves the title against District's real catalogue — not the model's
output, since an invented title would become a URL that 404s forever while the
watcher looked healthy — works out dates and the time window, confirms, and
**resets itself once the alert has fired** so you can point it at the next show.
`cancel` stops a watch.

Live events (concerts, cricket) are declined rather than half-supported:
District's `/events` pages use a different structure.

### Keyword commands — always work, even with Groq down

> report · status · check · update · news

With or without a `/`, anywhere in a sentence. These are matched **before** the
model is consulted, so the commands that tell you whether the watcher is alive
never depend on it.

## Messages you'll get

**The alert** — once, when shows appear:

```
🚨🕷️ IT'S LIVE! ENGLISH 4DX 3D TICKETS ARE OPEN! 🕷️🚨

🍿 Spider-Man: Brand New Day
🎟️ 4 shows between 06:00 and 20:00
⚡ GO BOOK NOW - 4DX sells out fast!

📅 Saturday, 8 Aug
  🎬 PVR Irrum Manzil
     🕒 10:10 AM, 1:25 PM, 4:45 PM ❌, 7:45 PM
  🔗 https://www.district.in/movies/...?fromdate=2026-08-08
```

**Shift reports** — automatic, at each IST boundary:

| Shift | Window |
|---|---|
| Morning | 07:00–12:00 |
| Afternoon | 12:00–18:00 |
| Evening | 18:00–21:00 |
| Night | 21:00–24:00 |

Each gives checks run, first/last check time, whether the site was reachable,
how far ahead tickets are on sale, and per-date status. Nothing is reported
between 00:00–07:00, though the watcher still runs and would still alert.

Shifts use a fixed `+05:30` offset — India has no DST, so it's exact and needs
no tzdata on a UTC host. (District's `showTime` is UTC and is converted before
the time window applies.)

## Config

Every value has a default in code; `.env` only overrides.

| Var | Meaning |
|---|---|
| `TELEGRAM_API_TOKEN` / `TELEGRAM_CHAT_ID` | required |
| `GROQ_API_KEY` / `GROQ_MODEL` | optional; enables plain-English chat |
| `SOURCE` | `district` (default) or `bms` |
| `DISTRICT_URL` | the city-specific movie page |
| `HOME_CITY` | used when a chat request names no city |
| `DATES` | `YYYYMMDD`, comma separated |
| `TIME_FROM` / `TIME_TO` | show start window, IST |
| `FORMAT` / `LANGUAGE` | punctuation-insensitive, so `4DX 3D` matches `4DX-3D` |
| `VENUES` | substring match; blank = every cinema |
| `SCAN_EVERY` / `LONG_POLL` | `--serve` only: 600 s and 25 s |

## Disclaimer

Personal, non-commercial use. Read-only — it never books, holds or buys.
Not affiliated with District or BookMyShow.

---

<div align="center">

**Built with 💻 and ☕ by Vishal Goud**

[![LinkedIn](https://img.shields.io/badge/LinkedIn-blue?style=flat&logo=linkedin)](http://www.linkedin.com/in/vishalgoud3105)
[![GitHub](https://img.shields.io/badge/GitHub-black?style=flat&logo=github)](https://github.com/Vishalgoud3105)
[![Portfolio](https://img.shields.io/badge/Portfolio-orange?style=flat)](https://vishalgoud3105.github.io/Portfolio/)

</div>
