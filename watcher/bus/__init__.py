"""The bus-fare domain: AbhiBus, cheapest price for a route+date.

RedBus was tried and dropped - confirmed IP-blocked from the Oracle VM even
with a proper cookie warm-up (see sources.py). AbhiBus alone covers this.

Sibling to the movie domain (watcher/movies/bms.py, district.py, ...), not built on a
shared Source interface with it - see the plan this came from. Two domains
today does not justify one; a third would.
"""
