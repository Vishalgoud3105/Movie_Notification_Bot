"""Movie showtime watcher -> Telegram.

Alerts the moment tickets open for a given movie, date and format.

Reads District by default: BookMyShow returns 403 to every datacenter IP, so it
cannot be used from a server. Set SOURCE=bms to read BookMyShow from a home
connection instead.

Usage:
  python watch.py             one check, for cron / Task Scheduler
  python watch.py --serve     stay running: instant chat replies
  python watch.py --test      send a test message + preview of the real alert
  python watch.py --report    status report right now
  python watch.py --selftest  offline logic checks, no network

Settings live in watcher/config.py, overridable via .env or the environment.
"""

import sys

from watcher.runner import main, report_now, serve, test_run

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from tests.test_watcher import demo
        demo()
    elif "--test" in sys.argv:
        test_run()
    elif "--report" in sys.argv:
        report_now()
    elif "--serve" in sys.argv:
        serve()
    else:
        main()
