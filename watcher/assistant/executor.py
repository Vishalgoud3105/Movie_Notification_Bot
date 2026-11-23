"""Runs a validated capability for the asking chat. Re-checks scope itself - never trusts the planner.

(Template: executor.py "re-authorizes". Here the authorization is the chat scope: every executor
takes the chat from the server-built Context, and a request that smuggled identity keys past the
validator is refused again here.)
"""

import logging

from .outcomes import Outcome
from .registry import ExecResult
from .validator import FORBIDDEN_KEYS

log = logging.getLogger(__name__)


def execute(capability, args, ctx):
    if ctx is None or ctx.chat_id is None or any(k.lower() in FORBIDDEN_KEYS for k in args):
        return ExecResult(Outcome.PERMISSION_DENIED, "I can only act for the chat that asked.")
    try:
        return capability.executor(ctx, args)
    except PermissionError:
        return ExecResult(Outcome.PERMISSION_DENIED, "I can only act for the chat that asked.")
    except Exception:
        log.exception("[assistant] executor %s failed", capability.name)
        return ExecResult(Outcome.SYSTEM_FAILURE,
                          "Something went wrong on my side while doing that - try again in a moment.")
