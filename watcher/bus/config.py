"""Bus-domain settings. Telegram/Groq/timing creds are shared - see watcher/config.py."""

import os

from ..config import *   # TELEGRAM_*, GROQ_*, SCAN_EVERY, LONG_POLL, IST, SHIFTS...

WATCH_FILE = os.environ.get("BUS_WATCH_FILE", "watch_bus.json")
STATE_FILE = os.environ.get("BUS_STATE_FILE", "seen_bus.json")
