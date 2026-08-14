"""Top-level entry points for watch.py.

One-shot modes (--test, --report, plain run) stay movie-only, same as before
there was a second domain - there is no bus equivalent of a cron job, since a
bus watch only exists once someone asks for one by chat. --serve is the one
mode that now runs both domains: a single Telegram long poll (never two
getUpdates consumers - see the deploy notes), routed by router.py, alongside
each domain's own scan cycle.
"""

import time

import requests

from . import router
from .bus import runner as bus_runner
from .bus import state as bus_state_mod
from .bus import watchspec as bus_watchspec
from .config import LONG_POLL, SCAN_EVERY
from .movies import runner as movies_runner
from .movies import watchspec as movie_watchspec
from .movies.config import SITE
from .movies.state import load_state, save_state
from .telegram import poll_commands

# Movie-only one-shot modes, unchanged in behavior.
main = movies_runner.main
report_now = movies_runner.report_now
test_run = movies_runner.test_run


def serve():
    """Stay running: instant chat replies for both domains, each scanned on its
    own rhythm. Mirrors watcher/movies/runner.py::serve()'s two-clock shape,
    extended to run the bus cycle alongside the movie one.
    """
    movies_runner.boot()
    bus_runner.boot()
    print("serving: chat replies are instant, %s and bus fares are each "
          "scanned every %d min whenever something's being watched. Ctrl-C to stop."
          % (SITE, SCAN_EVERY // 60))

    state = load_state()           # movie state also carries the one shared tg_offset
    bus_state = bus_state_mod.load_state()
    session = requests.Session()
    movie_hits, movie_broken, movie_bookable, movie_tally = {}, [], None, None
    bus_hits, bus_broken = [], []
    next_scan = 0.0

    while True:
        try:
            if time.time() >= next_scan:
                movie_hits, movie_broken, movie_bookable, movie_tally = \
                    movies_runner.run_cycle(state, session)
                bus_hits, bus_broken = bus_runner.run_cycle_bus(bus_state)
                next_scan = time.time() + SCAN_EVERY
            else:
                from .bus.shifts import maybe_report_shift as maybe_report_bus_shift
                from .movies.shifts import maybe_report_shift
                movie_tally = maybe_report_shift(state, watching=bool(movie_watchspec.load_all()))
                maybe_report_bus_shift(bus_state, watching=bool(bus_watchspec.load_all()))

            for chat_id, cmd in poll_commands(state, wait=LONG_POLL):
                router.answer(chat_id, cmd, {
                    "movie": (movie_hits, movie_broken, movie_bookable, movie_tally),
                    "bus": (bus_hits, bus_broken),
                })
            save_state(state)
            bus_state_mod.save_state(bus_state)
        except KeyboardInterrupt:
            print("stopped")
            return
        except Exception as e:      # a daemon must not die on one bad iteration
            print("cycle error: %s: %s" % (type(e).__name__, e))
            time.sleep(30)
