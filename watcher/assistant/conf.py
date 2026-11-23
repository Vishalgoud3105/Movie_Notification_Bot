"""Tunables for the assistant pipeline (the template's conf.py, as plain constants).

Every number here has a reason; change one only with the reason in hand.
"""

BRAND = "Notify AI"
DOMAIN = "movie ticket and bus fare watches"

# --- splitter (inside the planner's single call) ---------------------------------------
# "watch X and also watch bus Y" is two requests; three is the ceiling to keep the reply and the output small.
MAX_SUB_QUESTIONS = 3
MAX_MESSAGE_CHARS = 1000

# --- confidence gates (below -> ask, never guess) --------------------------------------
PLANNER_MIN_CONFIDENCE = 0.6

# --- caches and memory (all in-process: a restart forgets them, and the bot just asks again) -
DECISION_CACHE_TTL = 600        # seconds a successful LLM decision is reused
DECISION_CACHE_MAX = 500
PENDING_TTL = 15 * 60           # an unanswered clarification
MAX_CHATS = 500                 # memory entries kept before the oldest chat is forgotten

# --- loop breaker ----------------------------------------------------------------------
# The same clarification asked this many times in a row switches to an explicit "here is
# what I have, send exactly this" message, and one more drops the draft and starts clean.
# A conversation can no longer bounce between two questions forever.
CLARIFY_REPEAT_LIMIT = 2

# --- per-chat throttle (messages per rolling minute) -----------------------------------
THROTTLE_PER_MIN = 20

TRACE_RETENTION_DAYS = 15
