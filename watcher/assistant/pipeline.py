"""The assistant pipeline: one Telegram message in, one reply out, ONE LLM call.

    sanitize -> throttle -> injection guard -> [exact command? run it, no LLM]
      -> planner: the single LLM call (split + route + plan; sees the open clarification)
      -> per request:  validator -> executor -> deterministic summary
      -> memory (the one open clarification) -> one trace line

It replaces the old keyword router + two brains (23 Nov 2026), whose missing memory made the bot
loop between "Which dates?" and "Which movie?". The template's separate decomposer and intent-router calls
were measured and cut (see planner.py): three calls per message cost ~3.5s and, on the live matrix, did not
beat one. Every failure maps to a fixed message; nothing falls back to keyword routing - except `degraded`,
the status/cancel path that must keep working when no LLM is reachable (that is how you find out the watcher
is alive).
"""

import datetime as dt
import logging

from ..bus import actions as bus_actions
from ..movies import actions as movie_actions
from . import conf, executor, guards, memory, outcomes, planner, registry, trace, validator
from .context import Context
from .outcomes import Outcome

log = logging.getLogger(__name__)

# Whole-message commands run with NO model call - the template's "exact fast path" (its planner skips
# the LLM for an exact record id). Only an exact match: "any updates on my bus?" goes to the planner.
STATUS_WORDS = frozenset({"status", "/status", "report", "/report", "check", "/check", "update", "/update",
                          "updates", "any updates", "any update", "news"})
CANCEL_WORDS = frozenset({"cancel", "/cancel", "stop", "/stop", "abort", "reset"})
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class Part:
    """The answer to one request."""
    def __init__(self, text, outcome):
        self.text, self.outcome = text, outcome


# ------------------------------------------------------------------------------ entry point
def handle(chat_id, message, scans=None):
    """Answer one chat message. Always returns text; writes exactly one trace line."""
    token = trace.begin(chat_id, message)
    result = None
    try:
        result = _handle(chat_id, message, scans or {})
        return result[0]
    except Exception as exc:
        trace.note(error=type(exc).__name__)
        raise
    finally:
        trace.end(token, outcome=result[1].value if result else "exception")


def _handle(chat_id, message, scans):
    q = guards.sanitize(message)
    if not q:
        return outcomes.EMPTY, Outcome.NEEDS_CLARIFICATION
    if not guards.allow(chat_id):
        return outcomes.THROTTLED, Outcome.SYSTEM_FAILURE
    if guards.is_prompt_injection(q):
        trace.note(intent="INJECTION")
        return outcomes.INJECTION_REFUSAL, Outcome.UNSUPPORTED_QUERY

    ctx = Context(chat_id, scans)
    pending = memory.get_pending(chat_id)

    low = q.lower().strip().rstrip("?!. ")
    if low in STATUS_WORDS or low in CANCEL_WORDS:                 # exact command: no LLM, always works
        trace.note(planner="exact_command")
        part = _command(ctx, "status" if low in STATUS_WORDS else "cancel", pending)
        return part.text, part.outcome

    active = {k: v for k, v in (("movie", movie_actions.describe_active(chat_id)),
                                ("bus", bus_actions.describe_active(chat_id))) if v}
    plan = planner.plan(q, pending, active or None)                # the ONE LLM call
    if pending and not plan.requests and plan.chat in ("small_talk", "greeting", "about"):
        # a question is open and the model answered with chit-chat: the user's answer was misfiled, and anything
        # it carried (a venue, a format) would be lost. Ask once more, saying so. Rare path: normal messages stay at one call.
        trace.note(retry="chit-chat while a question was open")
        plan = planner.plan(q, pending, active or None, retry=True)
    trace.note(planner=plan.planner_used, plan_confidence=round(plan.confidence, 2),
               capability=[r.capability for r in plan.requests] or None,
               intent=("LIVE_DATA" if plan.requests else plan.chat.upper()))
    part = _answer(q, plan, ctx, pending)
    return part.text, part.outcome


def _answer(question, plan, ctx, pending):
    chat_id = ctx.chat_id
    if plan.too_many:
        return Part(outcomes.TOO_MANY % conf.MAX_SUB_QUESTIONS, Outcome.NEEDS_CLARIFICATION)

    if not plan.requests and pending and plan.chat in ("small_talk", "greeting", "about"):
        # a question is open and the model answered with chit-chat: that is almost always the user's ANSWER
        # misfiled (the primary model once replied "thanks for the dates!" to a bare title). Ask again, loop-guarded.
        return _clarify(chat_id, pending.capability, pending.args, pending.missing, pending.question, True)

    if not plan.requests:
        if plan.chat == "small_talk":                                   # the model's one sentence + a fixed way back in
            return Part("%s\n\nTell me what to watch - a movie or a bus route." % plan.reply, Outcome.SUCCESS)
        if plan.chat == "greeting":
            return Part(plan.reply, Outcome.SUCCESS)
        if plan.chat == "about":
            return Part(about_message(), Outcome.SUCCESS)
        if plan.chat == "out_of_scope":
            return Part(outcomes.OUT_OF_SCOPE, Outcome.UNSUPPORTED_QUERY)
        if plan.error == "llm_rate_limited":
            return Part(outcomes.RATE_LIMITED, Outcome.SYSTEM_FAILURE)
        if plan.error in ("llm_unavailable", "bad_plan"):
            return _degraded(question, ctx, pending)
        if pending:                                                     # unsure -> keep the draft, ask again (loop-guarded)
            return _clarify(chat_id, pending.capability, pending.args, pending.missing, pending.question, True)
        return Part(outcomes.UNCLEAR, Outcome.NEEDS_CLARIFICATION)

    parts = [_run(req, ctx, pending if i == 0 else None, store=(i == 0)) for i, req in enumerate(plan.requests)]
    # sequential on purpose: executors write the watch files
    ok = all(p.outcome == Outcome.SUCCESS for p in parts)
    text = "\n\n".join(p.text for p in parts)
    if plan.unsupported and any(p.outcome == Outcome.SUCCESS for p in parts):    # never silently drop what the user asked for
        text += "\n\n(I can't filter by %s, so I ignored it.)" % "; ".join(plan.unsupported)
    return Part(text, Outcome.SUCCESS if ok else parts[0].outcome)


# ------------------------------------------------------------------------------ one request
def _run(req, ctx, pending, store):
    """validate -> execute one request. `store`: only the FIRST request may open the (single) clarification."""
    chat_id = ctx.chat_id
    cap = registry.get(req.capability)

    # a bare "cancel" while a question is open drops THAT request and touches no running watch. A cancel that
    # names a target, says "everything", or names another domain ("cancel the bus watch") is a real stop.
    a = req.arguments
    if (pending and cap.name == "stop_watch" and not a.get("target") and not a.get("everything")
            and (a.get("domain") or "all") in ("all", pending.capability.replace("watch_", ""))):
        memory.clear_pending(chat_id)
        return Part(_dropped(chat_id), Outcome.SUCCESS)
    if pending and cap.name == "stop_watch":
        memory.clear_pending(chat_id)

    check = validator.validate(cap, req.arguments, dt.datetime.now(IST).date())
    if check.needs_clarification:
        if not check.missing:                                           # a forbidden key etc.: nothing to remember
            return Part(check.message, Outcome.NEEDS_CLARIFICATION)
        return _clarify(chat_id, cap.name, check.arguments, check.missing, check.message, store and cap.domain in ("movie", "bus"))

    result = executor.execute(cap, check.arguments, ctx)
    trace.note(executed=cap.name)
    if result.outcome == Outcome.NEEDS_CLARIFICATION and result.pending:
        return _clarify(chat_id, cap.name, result.pending["args"], result.pending["missing"], result.text, store)
    if result.outcome == Outcome.SUCCESS and cap.domain in ("movie", "bus") and store:
        memory.clear_pending(chat_id)
    text = result.text
    notes = check.notes + req.notes
    if notes and result.outcome == Outcome.SUCCESS:                # something the user said was left out: say so
        text += "\n\n(%s)" % " ".join(notes)
    return Part(text, result.outcome)


# ------------------------------------------------------------------------------ clarification + loop breaker
def _clarify(chat_id, cap_name, args, missing, question, store):
    """Ask for what is missing, and REMEMBER what was understood so the answer completes the request.
    The same question asked repeatedly escalates, then resets - it can never loop forever.
    # ponytail: one open question per chat - a second request that also needs details is asked without being
    # remembered (the user re-sends it), instead of overwriting the first"""
    if not store:
        return Part(question, Outcome.NEEDS_CLARIFICATION)
    p = memory.set_pending(chat_id, cap_name, args, missing, question)
    cap = registry.get(cap_name)
    if p.asked > conf.CLARIFY_REPEAT_LIMIT + 1:
        memory.clear_pending(chat_id)
        return Part("Let's start over - I keep missing something. Send the whole request in one message.",
                    Outcome.NEEDS_CLARIFICATION)
    if p.asked > conf.CLARIFY_REPEAT_LIMIT:
        got = cap.got(args) if cap and cap.got else ""
        return Part("%sI'm still missing: %s. Send just that in one line, or the whole request in one message."
                    % (got, ", ".join(m.replace("_", " ") for m in missing)), Outcome.NEEDS_CLARIFICATION)
    return Part(question, Outcome.NEEDS_CLARIFICATION)


def _dropped(chat_id):
    mine = movie_actions.describe_active(chat_id) + bus_actions.describe_active(chat_id)
    hint = (" (You still have %d watch%s running - say \"cancel everything\" to stop %s too.)"
            % (len(mine), "" if len(mine) == 1 else "es", "it" if len(mine) == 1 else "them")) if mine else ""
    return "Okay, dropped that request. Tell me what to watch next.%s" % hint


# ------------------------------------------------------------------------------ commands without a model
def _command(ctx, kind, pending):
    if kind == "cancel" and pending:
        memory.clear_pending(ctx.chat_id)
        return Part(_dropped(ctx.chat_id), Outcome.SUCCESS)
    if kind == "cancel":
        mine = movie_actions.describe_active(ctx.chat_id) + bus_actions.describe_active(ctx.chat_id)
        if len(mine) > 1:
            return Part("You're watching: %s. Which one should I stop? Name it, or say \"cancel everything\"."
                        % "; ".join(mine), Outcome.NEEDS_CLARIFICATION)
    cap = registry.get("show_status" if kind == "status" else "stop_watch")
    result = executor.execute(cap, {"domain": "all"}, ctx)
    return Part(result.text, result.outcome)


def _degraded(question, ctx, pending):
    """No LLM could be reached. Status and cancel are how you find out the watcher is alive, so they
    still work, matched as plain words; everything else gets the honest 'AI unavailable' message.
    This is a deliberate exception to 'no keyword routing': it exists ONLY when routing is impossible."""
    from ..telegram import wants_report
    trace.note(planner="degraded")
    low = question.lower()
    if wants_report(low):
        return _command(ctx, "status", pending)
    if any(w in low for w in ("cancel", "stop watching", "forget it", "abort")):
        return _command(ctx, "cancel", pending)
    return Part(outcomes.AI_UNAVAILABLE, Outcome.SYSTEM_FAILURE)


def fallback(chat_id, message, scans=None):
    """Used by watcher/router.py when handle() itself blew up: status and cancel must still work."""
    return _degraded(guards.sanitize(message), Context(chat_id, scans or {}), None).text


def about_message():
    """What the bot does, in plain words - no sample phrases to copy."""
    return ("I'm %s. I watch for movie tickets and bus fares and message this chat the moment they open - I never "
            "book anything.\n\n"
            "For a movie, tell me the title and the dates (venue, format, language and show time are optional). "
            "For a bus, tell me the route and the date (a price limit, AC or non-AC, sleeper or seater, and male or "
            "female seat are optional).\n\n"
            "Say status to see what I'm watching, or cancel to stop a watch." % conf.BRAND)
