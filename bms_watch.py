"""BookMyShow showtime watcher -> Telegram.

Alerts the moment tickets open for a given movie, date and format. Talks to the
JSON API the BMS Android app uses; the website returns 403 to any non-browser
client, which is what killed the original scraper.

Usage:
  python bms_watch.py             one check, for cron / Task Scheduler
  python bms_watch.py --serve     stay running: instant chat replies
  python bms_watch.py --test      send a test message + preview of the real alert
  python bms_watch.py --report    status report right now
  python bms_watch.py --selftest  offline logic checks, no network

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
