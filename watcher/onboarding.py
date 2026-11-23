"""What the bot says when it is added to a group or a new person messages it.

A fixed message, no LLM and no sample phrases: it describes what the bot needs, and the conversation takes it
from there. (It used to be LLM-written from a prompt that asked for a "realistic example phrase", and it called
llm.chat(), which was removed with the old brains - so welcoming a new chat raised AttributeError whenever an
API key was set. A fixed text cannot fail that way.)
"""

_WELCOME = "\n".join([
    "👋 Hi! I'm Notify AI. I watch for movie tickets and bus fares and message this chat the moment they open - I never book anything.",
    "",
    "🎬 Movies: tell me the title and the dates (venue, format, language and show time are optional).",
    "🚌 Buses: tell me the route and the date (a price limit, AC or non-AC, sleeper or seater, and male or female seat are optional).",
    "",
    "Anyone here can talk to me. Say status to see what I'm watching, or cancel to stop a watch. A report also arrives at the end of each shift.",
])


def welcome_message():
    return _WELCOME
