"""seen.json - what has been alerted, the shift tally, the chat offset."""

import json
import os

from .config import *


def load_state():
    """{'seen': [...], 'shift': {...}}, tolerating the old plain-list format."""
    if not os.path.exists(STATE_FILE):
        return {"seen": [], "shift": None}
    with open(STATE_FILE) as f:
        raw = json.load(f)
    if isinstance(raw, list):
        return {"seen": raw, "shift": None, "tg_offset": 0}
    return {"seen": raw.get("seen", []), "shift": raw.get("shift"),
            "tg_offset": raw.get("tg_offset", 0)}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump({"seen": state.get("seen", []), "shift": state.get("shift"),
                   "tg_offset": state.get("tg_offset", 0)}, f)
