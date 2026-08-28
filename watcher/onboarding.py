"""What the bot says when the owner moves it into a new group.

Generated fresh via the LLM each time, not a canned template - varied
wording every time, grounded in whatever is actually being watched right
now rather than always the same hardcoded route/movie. Falls back to a
static message if Mistral is unavailable, same as every other LLM use in this
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

Write a SHORT, SCANNABLE introduction - people skim group chats, not read
paragraphs. Structure it like this, using your own wording (vary it every
time you're asked, never settle into a fixed template):

- One line saying who you are and what you do, in one sentence.
- A "🎬 MOVIES" heading, then 1-2 short bullet points: how to ask (one
  realistic example phrase) and what happens (alert the moment it opens).
- A "🚌 BUS FARES" heading, then 1-2 short bullet points: how to ask (one
  realistic example phrase, mention a price target is optional) and what
  happens (alert on every new lowest fare, or once the target hits).
- One closing line: anyone here can talk to you, "status" for a report,
  "cancel" to stop a watch, and a report also arrives at each shift's end.

FACTS YOU MAY USE - real current state, not from you:
{facts}

If a watch is already active per the facts, use it as the real example under
that domain's bullet instead of inventing one. Never invent a movie, route,
price or date that is not in the facts.

Keep it under 12 lines total including headings and bullets. No walls of
text, no single long paragraph - use real line breaks between sections.
A little emoji is fine for headings, not one per line.
"""

_FALLBACK = "\n".join([
    "👋 Hey! I'm Notify AI - I watch for things to become available and ping "
    "this chat the moment they do. I never book anything, just watch.",
    "",
    "🎬 MOVIES",
    "• \"watch <movie> <format> at <venue> on <dates>\"",
    "• I alert the moment tickets open.",
    "",
    "🚌 BUS FARES",
    "• \"watch bus from <city> to <city> on <date>\", target price optional.",
    "• I alert on every new lowest fare, or once your target hits.",
    "",
    "Anyone here can talk to me - \"status\" for a report, \"cancel\" to stop "
    "a watch. A report also lands at the end of each shift.",
])


def welcome_message():
    """A fresh, LLM-phrased introduction, or a static fallback if Mistral is down."""
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
