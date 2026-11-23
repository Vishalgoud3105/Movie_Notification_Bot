"""The one door from the assistant to the LLM: watcher/llm.py's model chain with cross-provider
failover (_call), plus the two failure kinds the pipeline words differently."""

import json
import re

from . import trace


class LLMUnavailable(RuntimeError):
    """Every model on every provider failed."""


class LLMRateLimited(LLMUnavailable):
    """...and every one of them said 429: wait a minute, don't blame the AI."""


def generate(task, system, user, temperature=0.0, json_mode=False, max_tokens=500):
    from .. import llm
    out = llm._call([{"role": "system", "content": system}, {"role": "user", "content": user}],
                    temperature=temperature, json_mode=json_mode, max_tokens=max_tokens)
    if out is None:
        raise (LLMRateLimited if llm.last_failure == "rate_limited" else LLMUnavailable)(task)
    trace.note_append("llm", "%s=%s" % (task, llm.last_model))
    return out


def parse_json(raw):
    """Strict-ish JSON: tolerates a code fence, nothing else."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    return json.loads(text)
