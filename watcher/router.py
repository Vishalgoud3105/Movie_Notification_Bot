"""Where a Telegram message enters the assistant, and where the reply leaves.

This file used to guess a domain from keywords and hand the message to a per-domain brain; both
that guessing and the brains are gone (they could not remember a half-answered question, which made
the bot loop, and they mistook a film title for a bus request). One pipeline now owns the whole
conversation: see watcher/assistant/pipeline.py.

ctx_by_domain: {"movie": (hits, broken, bookable, tally), "bus": (hits, broken)} - the latest scan
results, display data for "status".
"""

from . import assistant
from .telegram import reply_to


def answer(chat_id, cmd, ctx_by_domain):
    print("command from you: %s" % cmd)
    try:
        reply = assistant.handle(chat_id, cmd, ctx_by_domain)
    except Exception as e:                       # never let chat break the watcher
        print("assistant failed, falling back to status/cancel: %s: %s" % (type(e).__name__, e))
        try:
            reply = assistant.fallback(chat_id, cmd, ctx_by_domain)
        except Exception:
            reply = "Something went wrong on my side. \"status\" and \"cancel\" should still work - try again in a moment."
    if reply:
        reply_to(chat_id, reply)
