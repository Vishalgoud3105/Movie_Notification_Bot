"""seen_bus.json - just the shift tally.

No per-fare dedup list like the movie domain's "seen" keys - watchspec's
`lowest_seen` already is the dedup for a bus watch (a fare is only "new" if
it undercuts that number), so there is nothing else to remember between
cycles.
"""

import json
import os

from .config import *


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"shift": None}
    with open(STATE_FILE) as f:
        raw = json.load(f)
    return {"shift": raw.get("shift")}


def save_state(state):
    """Write via a temp file and replace, so the state is never half-written."""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"shift": state.get("shift")}, f)
    os.replace(tmp, STATE_FILE)
