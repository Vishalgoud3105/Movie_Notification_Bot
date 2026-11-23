"""Who is asking and what the scan loop knows - built by the SERVER, never from the message.

Nothing the user types can change `chat_id`. `scans` is the latest result of each domain's scan
cycle (display data for "status", never used for any access decision) - the template's
UserContext.extras.
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class Context:
    chat_id: Any
    # {"movie": (hits, broken, bookable, tally), "bus": (hits, broken)} - see watcher/runner.py
    scans: Dict[str, tuple] = field(default_factory=dict)
