"""Outcomes and the fixed messages every failure maps to.

Nothing here is written by a model: a failure must never produce invented text, and it must
never fall back to "pick any capability". (Template rule: failures degrade to fixed, friendly
messages.)
"""

from enum import Enum


class Outcome(str, Enum):
    SUCCESS = "success"
    NO_DATA = "no_data"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED_QUERY = "unsupported_query"
    PERMISSION_DENIED = "permission_denied"
    SYSTEM_FAILURE = "system_failure"


INJECTION_REFUSAL = "I can't help with that. I only watch for movie tickets and bus fares."
OUT_OF_SCOPE = ("I only watch for things to become available - movie tickets and bus fares. "
                "Tell me which movie or which bus route you want watched.")
AI_UNAVAILABLE = ("Having trouble reaching my AI brain right now - try again in a bit. "
                  "\"status\"/\"cancel\" always work regardless, they don't need it.")
RATE_LIMITED = ("My AI provider is rate limiting me right now - give it a minute and send that again. "
                "\"status\"/\"cancel\" still work.")
THROTTLED = "That's a lot of messages at once - give me a few seconds and try again."
EMPTY = "Tell me which movie or bus route to watch."
TOO_MANY = ("I can handle up to %d separate requests in one message. "
            "Please split that into smaller messages.")
UNCLEAR = ("I'm not sure what you want me to watch. Tell me a movie (with dates) or a bus route (with a date), "
           "or say \"status\" / \"cancel\".")
NOTHING_ACTIVE = "😴 Nothing is being watched in this chat right now. Tell me which movie or bus route to watch."
