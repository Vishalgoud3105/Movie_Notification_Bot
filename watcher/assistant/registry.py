"""A capability is one thing the assistant can DO. The LLM only ever picks from these.

The template's ReadCapability is read-only by construction. This bot's job is to start and stop
watches, so a capability here may write - but only ever for the chat that asked (every executor
receives the server-built Context and nothing the user typed can name another chat; the validator
rejects identity keys; the executor re-checks the chat before it runs).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from .outcomes import Outcome


@dataclass
class ExecResult:
    """What an executor returns. `text` is built by code from real data - never by a model.

    `pending`, when set with NEEDS_CLARIFICATION, is {"args": {...to keep...}, "missing": [...]}:
    the pipeline stores it in memory so the user's next message completes the request instead of
    starting over (this is what ends the "Which dates?" / "Which movie?" loop).
    """
    outcome: Outcome
    text: str
    pending: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class Capability:
    """
    name           unique snake_case id the planner returns
    domain         "movie" | "bus" | "both" (for the about text and the planner catalog grouping)
    description    one line shown to the planner (first 90 chars used)
    args_schema    {"properties": {name: {type, enum, items, ...}}, "required": [...]}; validated in code
    executor       fn(ctx, args) -> ExecResult
    planner_hint   disambiguation line injected into the planner prompt (contrast with neighbours)
    examples       (utterance, {args}) few-shot pairs injected into the planner prompt
    ask            {missing_field: the question to ask for it}
    change_flag    argument name that means "edit my existing watch": required fields are then
                   filled from that watch instead of being asked for
    got            fn(args) -> "Got the dates (...). " : what was understood so far, put in front of
                   a clarification so the user sees nothing they said was lost
    identity       argument names that say WHICH request this is (a movie's title, a bus route): a
                   reply naming a different one is a new request, not an answer to the open question
    dates_must_be_named  dates the model fills in when the user's words name no day are dropped
    """
    name: str
    domain: str
    description: str
    args_schema: Dict[str, Any]
    executor: Callable
    planner_hint: str = ""
    examples: Tuple[Tuple[str, Dict[str, Any]], ...] = ()
    ask: Dict[str, str] = field(default_factory=dict)
    change_flag: str = ""
    got: Optional[Callable] = None
    identity: Tuple[str, ...] = ()
    dates_must_be_named: bool = False


_REGISTRY: Dict[str, Capability] = {}


def register(cap):
    _REGISTRY[cap.name] = cap
    return cap


def all_capabilities():
    from . import capabilities      # noqa: F401  (registers on import)
    return list(_REGISTRY.values())


def get(name):
    all_capabilities()
    return _REGISTRY.get(name)
