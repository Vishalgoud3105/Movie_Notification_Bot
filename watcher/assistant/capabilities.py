"""The capability catalog: everything the assistant can do. The planner picks ONE of these.

Accuracy lives HERE - in `planner_hint`, `examples` and the schemas - never in keyword lists
(template rule 5). The hints contrast each capability with its neighbours, which is what stops a
film title being read as a bus request and the reverse.
"""

from ..bus import actions as bus
from ..movies import actions as movie
from ..movies.messages import pretty_spec as movie_spec
from ..bus.messages import pretty_spec as bus_spec
from ..config import SCAN_EVERY
from . import outcomes
from .outcomes import Outcome
from .registry import Capability, ExecResult, register


# ------------------------------------------------------------------------------ executors
def _watch_movie(ctx, args):
    return movie.watch(ctx.chat_id, args)


def _watch_bus(ctx, args):
    return bus.watch(ctx.chat_id, args)


def _sections(ctx, domain):
    out = []
    if domain in ("movie", "all"):
        text = movie.status(ctx.chat_id, ctx.scans.get("movie"))
        if text:
            out.append(text)
    if domain in ("bus", "all"):
        text = bus.status(ctx.chat_id, ctx.scans.get("bus"))
        if text:
            out.append(text)
    return out


def _show_status(ctx, args):
    domain = args.get("domain") or "all"
    parts = _sections(ctx, domain)
    if not parts:
        what = {"movie": "No movie is being watched right now.", "bus": "No bus route is being watched right now."}
        return ExecResult(Outcome.NO_DATA, what[domain] + " Tell me what to watch." if domain in what
                          else outcomes.NOTHING_ACTIVE)
    return ExecResult(Outcome.SUCCESS, "\n\n".join(parts))


def _stop_watch(ctx, args):
    domain, target = args.get("domain") or "all", (args.get("target") or "").strip()
    stopped, matched = [], False
    if domain in ("movie", "all"):
        s, m = movie.stop(ctx.chat_id, target)
        stopped += [movie_spec(w) for w in s]
        matched = matched or m
    if domain in ("bus", "all") and not (target and stopped):
        s, m = bus.stop(ctx.chat_id, target)
        stopped += [bus_spec(w) for w in s]
        matched = matched or m
    if stopped:
        return ExecResult(Outcome.SUCCESS, "Stopped watching: %s.\n\nTell me what to watch next." % "; ".join(stopped))
    if target and not matched:
        active = movie.describe_active(ctx.chat_id) + bus.describe_active(ctx.chat_id)
        mine = ("You're watching: %s." % "; ".join(active)) if active else "Nothing is being watched in this chat."
        return ExecResult(Outcome.NO_DATA, "I'm not watching anything matching \"%s\". %s" % (target, mine))
    what = {"movie": "no movie is being watched", "bus": "no bus route is being watched"}.get(domain, "nothing is being watched")
    return ExecResult(Outcome.NO_DATA, "Nothing to cancel - %s." % what)


def _explain(ctx, args):
    """Why-isn't-it-alerting questions: the real status plus how the watcher works, all from code -
    no model writes a guess about the system."""
    parts = _sections(ctx, "all")
    how = ("How it works: I scan every %d minutes and message this chat the moment something you're "
           "watching opens. No alert yet just means nothing matching has opened. A shift report at the end of "
           "each shift proves I'm alive - if one is missing, I've stopped." % (SCAN_EVERY // 60))
    return ExecResult(Outcome.SUCCESS, "\n\n".join(parts + [how]) if parts else outcomes.NOTHING_ACTIVE + "\n\n" + how)


# ------------------------------------------------------------------------------ catalog
_STR = {"type": "string"}
_DATES = {"type": "array", "items": {"type": "string"}}

register(Capability(
    name="watch_movie", domain="movie",
    description="Watch a movie/show for tickets: title, dates, optional venue, format, language, time window, seat tier",
    args_schema={"properties": {
        "title": {"type": "string", "description": "film or show name as the user said it"},
        "city": _STR,
        "dates": {"type": "array", "items": {"type": "string"}, "description": "YYYY-MM-DD each"},
        "format": {"type": "string", "description": "screen format, e.g. 4DX 3D, IMAX, 2D"},
        "language": _STR,
        "venues": {"type": "array", "items": {"type": "string"}, "description": "distinctive part of the cinema name"},
        "time_from": {"type": "string", "description": "HH:MM 24h"}, "time_to": {"type": "string", "description": "HH:MM 24h"},
        "seat_category": {"type": "string", "description": "seat tier the user named (recliner, gold...), lowercase"},
        "change_existing": {"type": "boolean", "description": "true ONLY when editing a movie watch that already exists"},
    # dates are NOT required here: the executor looks the title up on District first and then asks
    # "Found X. Which dates?" - confirming the film exists before asking for more
    }, "required": ["title"]},
    executor=_watch_movie, change_flag="change_existing", got=movie.got_text,
    identity=("title",), dates_must_be_named=True,
    planner_hint=("ANY film request: a film title, a cinema or venue, a screen format (4DX, IMAX, 3D), a language, "
                  "show timings, or dates/time given for a film. A bare title, a bare date or a bare venue is a movie "
                  "request (it continues the film being discussed). Never for travel between two places. "
                  "'only evening' / 'at PVR instead' about an existing movie watch = change_existing true."),
    examples=(
        ("watch spiderman 4dx at irrum manzil on {d1} and {d2} morning to evening",
         {"title": "spiderman", "dates": ["<d1>", "<d2>"], "format": "4dx", "venues": ["irrum manzil"],
          "time_from": "06:00", "time_to": "20:00"}),
        ("I want recliner seats for jawan on {d1}", {"title": "jawan", "seat_category": "recliner", "dates": ["<d1>"]}),
        ("make it evening only", {"time_from": "17:00", "time_to": "21:00", "change_existing": True}),
        ("{d1} and {d2}", {"dates": ["<d1>", "<d2>"]}),
        ("only infinity vision 4dx3d at irrum manzil any time", {"format": "infinity vision 4dx3d", "venues": ["irrum manzil"]}),
    ),
    ask={"title": "Which movie or show should I watch? Give me a name and I'll find it.",
         "dates": "Which dates should I watch?"},
))

register(Capability(
    name="watch_bus", domain="bus",
    description="Watch bus fares for a route and date: from, to, date, optional price ceiling, AC/sleeper/seater, male/female seat",
    args_schema={"properties": {
        "from_city": {"type": "string", "description": "departure CITY, lowercase, full name (hyd -> hyderabad)"},
        "to_city": {"type": "string", "description": "destination CITY, lowercase, never a state or region"},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "target_price": {"type": "number", "description": "price ceiling in rupees, only if the user gave one"},
        "ac": {"type": "string", "enum": ["ac", "non_ac"]},
        "seat_type": {"type": "string", "enum": ["sleeper", "seater"]},
        "gender": {"type": "string", "enum": ["male", "female"], "description": "male/gents seat or female/ladies seat"},
        "change_existing": {"type": "boolean", "description": "true ONLY when editing a bus watch that already exists"},
    }, "required": ["from_city", "to_city", "date"]},
    executor=_watch_bus, change_flag="change_existing", got=bus.got_text,
    identity=("from_city", "to_city"), dates_must_be_named=True,
    planner_hint=("BUS travel only: two places (from/to), a fare or price ceiling, AC / sleeper / seater, male or "
                  "female seat, 'cheap bus'. A price alone ('under 700', 'make it cheaper') with a bus watch active = "
                  "change_existing true. Never for films, cinemas or screen formats."),
    examples=(
        ("watch bus from hyd to vizag for male seat tomorrow",
         {"from_city": "hyderabad", "to_city": "visakhapatnam", "date": "<tomorrow>", "gender": "male"}),
        ("bangalore to chennai this saturday AC sleeper under 900",
         {"from_city": "bangalore", "to_city": "chennai", "date": "<saturday>", "ac": "ac", "seat_type": "sleeper", "target_price": 900}),
        ("make it under 700 now", {"target_price": 700, "change_existing": True}),
    ),
    ask={"from_city": "Which route should I watch? Give me a \"from\" and \"to\" city.",
         "to_city": "Which route should I watch? Give me a \"from\" and \"to\" city.",
         "date": "Which date should I watch?"},
))

register(Capability(
    name="show_status", domain="both",
    description="Show what is being watched and how it is going (status, report, any updates)",
    args_schema={"properties": {"domain": {"type": "string", "enum": ["movie", "bus", "all"], "default": "all"}}},
    executor=_show_status,
    planner_hint=("Reading, not changing: 'status', 'report', 'any updates', 'what am I watching', 'is it open yet'. "
                  "domain 'all' unless the user clearly means only movies or only buses."),
    examples=(("status", {"domain": "all"}), ("any updates on my bus?", {"domain": "bus"})),
))

register(Capability(
    name="stop_watch", domain="both",
    description="Stop watching: cancel one named watch, a whole domain, or everything in this chat",
    args_schema={"properties": {
        "domain": {"type": "string", "enum": ["movie", "bus", "all"], "default": "all"},
        "target": {"type": "string", "description": "words naming ONE watch (a movie title or a city), only if named"},
        "everything": {"type": "boolean", "description": "true ONLY when the user says everything / all / every watch"}}},
    executor=_stop_watch,
    planner_hint=("Stopping: cancel, stop, forget it, remove. Put a movie title or a city in `target` only when the "
                  "user names one ('cancel spiderman', 'drop the bangalore one'); 'cancel everything' = domain all, everything true. "
                  "A bare 'cancel' / 'stop' names no domain and no target."),
    examples=(("cancel spiderman", {"domain": "movie", "target": "spiderman"}),
              ("cancel the bangalore one", {"domain": "bus", "target": "bangalore"}),
              ("cancel everything", {"domain": "all", "everything": True}),
              ("cancel the bus watch", {"domain": "bus"})),
))

register(Capability(
    name="explain_alerts", domain="both",
    description="Explain why no alert has come / whether the watcher is working",
    args_schema={"properties": {}},
    executor=_explain,
    planner_hint=("Troubleshooting questions: 'why am I not getting alerts', 'is it working', 'it stopped', 'nothing "
                  "came'. Not for starting or stopping a watch."),
    examples=(("why am i not getting alerts", {}), ("is the bot working?", {})),
))
