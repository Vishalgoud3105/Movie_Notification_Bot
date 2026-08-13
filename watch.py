"""Notify AI -> Telegram: watches for movie tickets and bus fares, alerts once.

Movies: alerts the moment tickets open for a given movie, date and format.
Reads District by default: BookMyShow returns 403 to every datacenter IP, so it
cannot be used from a server. Set SOURCE=bms to read BookMyShow from a home
connection instead.

Bus: alerts on the cheapest AbhiBus fare for a chat-set route+date.
Only --serve runs it - there is no default route the way movies default to
Spider-Man, so nothing scans until you ask for a watch by chat.

Usage:
  python watch.py             one check, for cron / Task Scheduler (movies only)
  python watch.py --serve     stay running: instant chat replies, both domains
  python watch.py --test      send a test message + preview of the real alert (movies only)
  python watch.py --report    status report right now (movies only)
  python watch.py --selftest  offline logic checks, no network

Settings live in watcher/config.py (shared) plus watcher/movies/config.py and
watcher/bus/config.py, overridable via .env or the environment.
"""

import sys

from watcher.runner import main, report_now, serve, test_run

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from tests.test_watcher import demo
        from tests.test_bus import demo as demo_bus
        demo()
        demo_bus()
    elif "--test" in sys.argv:
        test_run()
    elif "--report" in sys.argv:
        report_now()
    elif "--serve" in sys.argv:
        serve()
    else:
        main()
