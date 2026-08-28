"""Mistral (mistral-medium-latest by default) for understanding messages and
phrasing replies.

Was Groq until 29 Aug 2026 - switched providers after Groq deprecated
llama-3.3-70b-versatile and its suggested replacement (openai/gpt-oss-120b)
turned out to be a reasoning model, burning tokens on hidden reasoning before
any visible output - see config.py's MISTRAL_MODEL comment and
[[project-bms-gotchas]].

Deliberately thin: Mistral speaks the OpenAI chat-completions shape, so
`requests` is enough and the project keeps its single dependency.

Every entry point degrades to None/False rather than raising. If Mistral is
down, misconfigured or slow, the watcher must keep watching and keep
answering the deterministic keyword commands - the LLM is a convenience
layer, never a dependency of the alert path.

Shared by every domain: the prompt text is what differs (movie vs bus vs
whatever comes next), not this calling code, so extract()/chat()/troubleshoot()
take the prompt templates as arguments. Defaults point at the movie prompts so
existing call sites don't have to change.
"""

import json
import os
import re
import time

import requests

from .config import *
from .movies.prompt_template import (CHAT_SYSTEM, CHAT_USER, EXTRACT_SYSTEM,
                                     EXTRACT_USER, TROUBLESHOOT_SYSTEM)

API = "https://api.mistral.ai/v1/chat/completions"


def available():
    """True if a key is configured. Never logs or returns the key itself."""
    return bool(os.environ.get("MISTRAL_API_KEY"))


def _call(messages, temperature=0.4, json_mode=False, max_tokens=700):
    """One chat completion, or None on any failure."""
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        return None
    body = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    for attempt in range(3):
        try:
            r = requests.post(API, json=body, timeout=45,
                              headers={"Authorization": "Bearer %s" % key})
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            # 401/400 will not fix themselves; 429/5xx might
            print("mistral HTTP %s: %s" % (r.status_code, r.text[:160]))
            if r.status_code in (400, 401, 403, 404):
                return None
        except (requests.RequestException, ValueError, KeyError) as e:
            print("mistral call failed (attempt %d): %s" % (attempt + 1, e))
        time.sleep(2 * (attempt + 1))
    return None


def _loads(text):
    """Parse JSON that may arrive wrapped in prose or fences."""
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    m = re.search(r"\{.*\}", text, re.S)       # first {...} block
    if m:
        try:
            return json.loads(m.group())
        except ValueError:
            return None
    return None


def extract(message, today, weekday, system=EXTRACT_SYSTEM, user_template=EXTRACT_USER):
    """A message -> watch-spec dict, or None if the model could not be used.

    json_mode plus a low temperature: this is parsing, not writing, and a
    creative answer here silently watches the wrong thing. `system`/
    `user_template` let another domain (e.g. bus) supply its own schema -
    defaults are the movie ones so existing call sites need not change.
    """
    out = _call(
        [{"role": "system", "content": system},
         {"role": "user", "content": user_template.format(
             today=today, weekday=weekday, message=message)}],
        temperature=0.0, json_mode=True, max_tokens=500)
    spec = _loads(out)
    return spec if isinstance(spec, dict) else None


def chat(message, facts, owner_context="a movie fan in Hyderabad", system=CHAT_SYSTEM):
    """Conversational reply grounded in facts, or None."""
    return _call(
        [{"role": "system", "content": system.format(
            facts=facts or "(no facts available)", owner_context=owner_context)},
         {"role": "user", "content": CHAT_USER.format(message=message)}],
        temperature=0.5, max_tokens=350)


def troubleshoot(message, facts, system=TROUBLESHOOT_SYSTEM):
    """Diagnostic help grounded in what the watcher actually is, or None."""
    return _call(
        [{"role": "system", "content": system.format(
            facts=facts or "(none captured)", scan_min=SCAN_EVERY // 60)},
         {"role": "user", "content": message}],
        temperature=0.3, max_tokens=400)


def classify_domain(message):
    """Which domain (\"movie\" | \"bus\") a chat message is about, or None.

    A single cheap classification call, used by router.py only when keyword
    matching and "one active watch" both come up empty. Never guesses when
    Mistral is unavailable - the caller falls back to movie, today's only
    established default.
    """
    out = _call(
        [{"role": "system", "content":
          "Reply with exactly one word: \"movie\" or \"bus\". Movie = a film, "
          "show or cinema ticket. Bus = a bus fare, route or travel booking "
          "between two places. If genuinely unclear, reply \"movie\"."},
         {"role": "user", "content": message}],
        temperature=0.0, max_tokens=5)
    if not out:
        return None
    out = out.strip().lower()
    return out if out in ("movie", "bus") else None
