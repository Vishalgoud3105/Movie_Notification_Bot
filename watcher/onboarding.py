"""What the bot says when the owner moves it into a new group.

Generated fresh via the LLM each time, not a canned template - varied
phrasing and examples, and grounded in whatever is actually being watched
right now rather than always the same hardcoded route/movie. Falls back to a
static message if Groq is unavailable, same as every other LLM use in this
project: never a dependency of anything the bot actually has to do.

Kept separate from telegram.py (pure transport) and each domain's own
messages.py (each scoped to one domain) - this is the one place that talks
about every domain at once, same reasoning as watcher/router.py.
"""

WELCOME_SYSTEM = """\
You are introducing yourself to a Telegram group the owner just added you to.
You are Notify AI: you watch for things to become available - movie tickets,
bus fares, more domains may come later - and alert the moment they do. You
never book anything, only watch.

Write a warm, concrete welcome message, 6-10 short lines. Vary your wording
and examples every time you're asked this - do not settle into a template.
Cover, in your own words:
- what you do and why it's useful (never miss a ticket/fare drop)
- one realistic example of a movie watch request, in plain English
- one realistic example of a bus watch request, in plain English, including
  that a price target is optional
- that anyone in this group can talk to you, ask "status", or say "cancel"
- that a status report also arrives automatically at the end of each shift

FACTS YOU MAY USE - real current state, not from you:
{facts}

If a watch is already active per the facts, mention it naturally as a live
example of what you're already doing, instead of inventing a fresh one.
Never invent a movie, route, price or date that is not in the facts.
No markdown. A little emoji is fine for warmth, not one per line.
"""

_FALLBACK = "\n".join([
    "👋 Hey! I'm Notify AI - I watch for things to become available and ping "
    "this chat the moment they do. I never book anything, I just watch.",
    "",
    "🎬 Tell me a movie to watch: name, dates, format, venue.",
    "🚌 Tell me a bus route to watch: from, to, date, and optionally a price "
    "target - I'll alert on every new lowest fare, or once your target is hit.",
    "",
    "Anyone here can talk to me - ask \"status\" any time, or \"cancel\" to stop "
    "a watch. I'll also send a report at the end of each shift so you know "
    "I'm still alive even when nothing's changed yet.",
])


def welcome_message():
    """A fresh, LLM-phrased introduction, or a static fallback if Groq is down."""
    from . import llm
    from .bus import watchspec as bus_watchspec
    from .movies import watchspec as movie_watchspec

    if llm.available():
        facts = "%s\n%s" % (movie_watchspec.describe(), bus_watchspec.describe())
        text = llm.chat("Introduce yourself to this new group.", facts,
                        owner_context="a group the owner just added you to",
                        system=WELCOME_SYSTEM)
        if text:
            return text
    return _FALLBACK
