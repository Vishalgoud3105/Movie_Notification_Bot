# Notify AI 🕷️🚌

Watches for something to become available and pings Telegram the moment it
does — then goes quiet again. Started as a single-purpose movie-ticket
watcher; now a small framework of independent domains sharing one bot.

Tell it what to watch in plain English, or set it in `.env`. It never books
anything; it only watches.

> Built to catch **Spider-Man: Brand New Day** in **English 4DX 3D** at
> **PVR Irrum Manzil, Hyderabad**. It worked — the alert fired and the tickets
> were booked.

## Domains

- **Movies** (`watcher/movies/`) — District/BookMyShow showtimes. Live in
  production. Has a standing default watch (set via `.env`) as well as
  chat-set ones. Multiple movies can be watched at once. Real per-category
  seat pricing and an optional seat-category filter ("watch it in recliner
  seats") come from District's own session data — see Filters below.
- **Bus fares** (`watcher/bus/`) — AbhiBus cheapest price for a route+date,
  chat-set only (no standing default). Multiple routes can be watched at
  once. Government/state RTC operators are excluded and a minimum rating
  bar is enforced before a bus counts as "cheapest" — see Filters below.
  RedBus was tried and dropped: confirmed IP-blocked from the VM even with a
  proper cookie warm-up — see `watcher/bus/sources.py`.

Each domain is a self-contained sibling package with its own watch file, own
state file, own message templates — not one shared `Source` interface, on
purpose: see `watcher/router.py`'s docstring for why. `watcher/router.py`
classifies an incoming chat message (movie vs bus) so both domains can share
one Telegram poll; `watcher/config.py`, `watcher/telegram.py` and
`watcher/llm.py` are the generic infrastructure every domain reuses.

### Multi-watch and chat isolation

Several movies and several bus routes can be watched at once, each
independent. A watch remembers which Telegram chat created it — alerts,
`status`, and `cancel` are all scoped to that chat, so watching from your own
DM never alerts into a group the bot is also in, and vice versa. A bare
`cancel` with nothing named stops every watch *this chat* can see, never
another chat's. (Watches created before this existed have no chat tag and
stay visible from anywhere, as a one-time migration fallback.)

### Filters

**Bus (AbhiBus):** optional `ac` (`ac`/`non_ac`), `seat_type`
(`sleeper`/`seater`), and `gender` (`male`/`female`) filters — "watch it,
male seats only" reports the cheapest matching seat specifically, with a
direct link to that bus's seat-selection page. Government/state RTC
operators (TSRTC, KSRTC, etc.) never appear in results, and a bus must clear
a minimum rating (3.5★, 5+ ratings) before it's ever reported as cheapest —
an unrated bus doesn't pass by default.

**Movies (District):** optional free-text seat-category filter — "watch it
in recliner seats", "gold seats". Every cinema chain names its own tiers
(CLASSIC ROWS/PRIME ROWS/RECLINER ROWS at one INOX, EXECUTIVE at another),
so this matches as a substring rather than a fixed list. Alerts always show
the real price per show now (District's session data carried this all
along, just wasn't being read), and show both a BookMyShow and a District
link — District's is exact for that watch; BMS's is a general city listing,
since BMS blocks every request from this project's server and there's no way
to resolve a real per-movie BMS page from it (a true tap-to-book seat link,
the way bus has, was investigated for both platforms and isn't achievable
automatically — District's seat page is session-bound, BMS's would need
codes this server can't fetch).

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
  config.py             SHARED: Telegram/Mistral creds, timing, IST clock, keywords
  telegram.py           SHARED: send, poll — pure transport, no domain knowledge
  llm.py                SHARED: Mistral client, prompts passed in per domain
  router.py             classifies a chat message movie-vs-bus, one shared poll
  runner.py             top-level entry points; --serve runs both domains

  movies/                the original watcher, unmoved in behavior
    config.py            movie settings, layered on the shared config
    district.py          the live source: fetch + parse district.in
    bms.py                the BookMyShow reader (SOURCE=bms, home only)
    sources.py            scan() — picks a source, one pass over the dates
    messages.py           alerts, shift reports, formatting
    shifts.py             shift boundary handling
    state.py              seen.json
    runner.py             the movie run modes (one-shot, --test, --report, --serve loop)
    prompt_template.py    extraction / chat / troubleshooting prompts
    brain.py              routes a message: keywords → LLM → fallback
    watchspec.py          the live watch; set by chat, reset when the goal fires
    search.py             resolves a title against District's real catalogue

  bus/                    AbhiBus cheapest-fare watcher
    config.py             bus settings, layered on the shared config
    abhibus.py             the source - real, working, live-verified
    sources.py            scan() — runs AbhiBus, wraps its result
    messages.py           new-low/target-met alerts, shift and status reports
    shifts.py             shift boundary handling
    state.py              seen_bus.json (just the shift tally)
    runner.py             run_cycle_bus(): compare-and-alert logic
    prompt_template.py    extraction / chat / troubleshooting prompts
    brain.py              routes a message: keywords → LLM → fallback
    watchspec.py          the live route watch; set by chat, reset when cancelled

tests/test_watcher.py   movie offline self-checks, no framework
tests/test_bus.py       bus offline self-checks, no framework
deploy/watcher.service  systemd unit
```

Only dependency is **`requests`** — Mistral speaks the OpenAI chat shape, so no SDK.
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

Needs `MISTRAL_API_KEY` (console.mistral.ai). Describe what you want:

> *"watch Spider-Man 4DX 3D at Irrum Manzil on 8 and 9 Aug, morning to evening"*

It resolves the title against District's real catalogue — not the model's
output, since an invented title would become a URL that 404s forever while the
watcher looked healthy — works out dates and the time window, confirms, and
**resets itself once the alert has fired** so you can point it at the next show.
`cancel` stops a watch.

Live events (concerts, cricket) are declined rather than half-supported:
District's `/events` pages use a different structure.

### Keyword commands — always work, even with Mistral down

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
| `MISTRAL_API_KEY` / `MISTRAL_MODEL` | optional; enables plain-English chat |
| `SOURCE` | `district` (default) or `bms` |
| `DISTRICT_URL` | the city-specific movie page |
| `HOME_CITY` | used when a chat request names no city |
| `DATES` | `YYYYMMDD`, comma separated |
| `TIME_FROM` / `TIME_TO` | show start window, IST |
| `FORMAT` / `LANGUAGE` | punctuation-insensitive, so `4DX 3D` matches `4DX-3D` |
| `VENUES` | substring match; blank = every cinema |
| `SEAT_CATEGORY` | free-text seat tier, e.g. `recliner`; blank = any |
| `SCAN_EVERY` / `LONG_POLL` | `--serve` only: 600 s and 25 s |

These are only the *default* env-configured movie watch. Bus has no env
defaults — every bus watch is chat-set (`ac`/`seat_type`/`gender` filters
included), and movie watches set via chat carry their own filters
independent of these `.env` values too.

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
