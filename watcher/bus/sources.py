"""Runs AbhiBus for one route+date.

RedBus (watcher/bus/redbus.py) was dropped: confirmed IP-blocked from the
Oracle VM even with the same cookie warm-up that makes AbhiBus work (see
abhibus.py's docstring history / the universal-notifier-bot chat log, 14 Aug
2026) - the same ceiling BookMyShow hit for the movie domain. One working
source beats a second one that can't run in production.

Kept as its own module (not folded into brain.py/runner.py) so a second
source is a one-line addition here again if one is ever confirmed working -
same shape movies/sources.py already proved out.
"""

from . import abhibus


def scan(from_city, to_city, date_code, ac=None, seat_type=None, gender=None):
    """One pass over AbhiBus. Returns (hits: list[Hit], broken: list[str]).

    ac/seat_type/gender are optional filters straight from the watch spec -
    see abhibus.search()'s docstring for what each costs (ac is free,
    seat_type/gender cost one extra request per bus checked, bounded).

    Hits are already private-operator-only and already above abhibus.py's
    quality bar (MIN_RATING/MIN_RATING_COUNT) - both are enforced inside
    search() itself, before the seat-layout refinement step, so a below-bar
    bus never spends one of those bounded, rate-limit-prone requests only to
    be dropped afterward anyway. If a second source is ever added here, it
    needs to enforce its own bar the same way - there's nothing left to
    filter at this merge point once there's only one source doing the work.
    """
    try:
        found = abhibus.search(from_city, to_city, date_code,
                               ac=ac, seat_type=seat_type, gender=gender)
    except NotImplementedError as e:
        print("abhibus not wired up yet: %s" % e)
        return [], ["abhibus"]
    if found is None:
        return [], ["abhibus"]
    return found, []
