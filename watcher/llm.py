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

import datetime as dt
import json
import os
import re
import time

import threading
from functools import lru_cache

import requests

from . import jev
from .config import *
from .movies.prompt_template import (CHAT_SYSTEM, CHAT_USER, EXTRACT_SYSTEM,
                                     EXTRACT_USER, TROUBLESHOOT_SYSTEM)

API = "https://api.mistral.ai/v1/chat/completions"


def available():
    """True if a key is configured. Never logs or returns the key itself."""
    return bool(os.environ.get("MISTRAL_API_KEY"))


def _models(prefer=None):
    """`prefer` (if any) first, then the primary and fallbacks, no duplicates."""
    return list(dict.fromkeys(([prefer] if prefer else []) + [MISTRAL_MODEL] + MISTRAL_FALLBACK_MODELS))


@lru_cache(maxsize=128)
def _route(message):
    """The model Jev says should go first for this message, or None.

    Cached per message text so extract() and a follow-up chat() on the SAME
    message cost one Jev call, not two.
    """
    return {"simple": JEV_SIMPLE_MODEL, "complex": JEV_COMPLEX_MODEL}.get(jev.complexity(message))


def _preferred(message):
    """Model to try first for `message`, or None = today's static order.

    off: never asks Jev. shadow (the default until --jev-eval has been run with
    a real key): asks Jev on a background thread purely to log what it WOULD
    have chosen, so it adds no latency and changes nothing. on: asks Jev and
    acts on it, waiting at most jev.TIMEOUT. Any failure inside is already
    swallowed by jev.complexity(), which returns None.
    """
    if not jev.available():
        return None
    if jev.mode() == "shadow":
        threading.Thread(target=_route, args=(message,), daemon=True).start()
        return None
    return _route(message)


def _call(messages, temperature=0.4, json_mode=False, max_tokens=700, prefer=None):
    """One chat completion, or None on any failure.

    Walks the model chain (see config.py's MISTRAL_MODEL comment): a free-tier
    key can have one model throttled (429) or tier-blocked (403) while another
    works fine, and a single hardcoded model turned that into a total silent
    outage. Those refusals are per-model and won't clear in seconds, so move
    straight to the next model - no sleep. A 401 means the KEY is bad, which
    no model can fix, so stop. Only 5xx/network errors are worth retrying the
    same model.
    """
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        return None
    body = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    for model in _models(prefer):
        body["model"] = model
        for attempt in range(3):
            try:
                r = requests.post(API, json=body, timeout=45,
                                  headers={"Authorization": "Bearer %s" % key})
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"].strip()
                print("mistral HTTP %s (%s): %s" % (r.status_code, model, r.text[:160]))
                if r.status_code == 401:
                    return None
                if r.status_code in (400, 403, 404, 429):
                    break                       # this model won't work now - next one
            except (requests.RequestException, ValueError, KeyError) as e:
                print("mistral call failed (%s, attempt %d): %s" % (model, attempt + 1, e))
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


def _calendar(today, days=14):
    """'2026-09-30' -> 'Wed 2026-09-30 (today)\nThu 2026-10-01\n...' (14 lines).

    The ministral models get weekday arithmetic wrong (measured 30 Sep 2026:
    "this Saturday" from a Wednesday came back as the 4th/6th/10th instead of
    the 3rd, on every model) and a wrong date silently watches the wrong day,
    so hand them a lookup table instead of asking them to compute it. Costs
    ~14 short lines of prompt per extract call - accepted on purpose.
    """
    start = dt.date.fromisoformat(today)
    return "\n".join("%s %s%s" % (d.strftime("%a"), d.isoformat(), " (today)" if i == 0 else "")
                     for i, d in enumerate(start + dt.timedelta(days=n) for n in range(days)))


def extract(message, today, weekday, system=EXTRACT_SYSTEM, user_template=EXTRACT_USER):
    """A message -> watch-spec dict, or None if the model could not be used.

    json_mode plus a low temperature: this is parsing, not writing, and a
    creative answer here silently watches the wrong thing. `system`/
    `user_template` let another domain (e.g. bus) supply its own schema -
    defaults are the movie ones so existing call sites need not change.
    """
    out = _call(_extract_messages(system, user_template, message, today, weekday),
                temperature=0.0, json_mode=True, max_tokens=500, prefer=_preferred(message))
    spec = _loads(out)
    return spec if isinstance(spec, dict) else None


def _extract_messages(system, user_template, message, today, weekday):
    return [{"role": "system", "content": system},
            {"role": "user", "content": user_template.format(
                today=today, weekday=weekday, message=message, calendar=_calendar(today))}]


def chat(message, facts, owner_context="a movie fan in Hyderabad", system=CHAT_SYSTEM):
    """Conversational reply grounded in facts, or None."""
    return _call(
        [{"role": "system", "content": system.format(
            facts=facts or "(no facts available)", owner_context=owner_context)},
         {"role": "user", "content": CHAT_USER.format(message=message)}],
        temperature=0.5, max_tokens=350, prefer=_preferred(message))


def troubleshoot(message, facts, system=TROUBLESHOOT_SYSTEM):
    """Diagnostic help grounded in what the watcher actually is, or None."""
    return _call(
        [{"role": "system", "content": system.format(
            facts=facts or "(none captured)", scan_min=SCAN_EVERY // 60)},
         {"role": "user", "content": message}],
        temperature=0.3, max_tokens=400, prefer=_preferred(message))


def _masked(value):
    """First/last 4 chars only, length shown - enough to eyeball-compare
    against the console without ever printing the real secret. A length or
    prefix/suffix mismatch against what you see on console.mistral.ai is
    itself the finding (stale key, partial paste, wrong line edited)."""
    if not value:
        return "(not set)"
    if len(value) <= 10:
        return "*" * len(value) + " (%d chars - looks too short for a real key)" % len(value)
    return "%s...%s (%d chars)" % (value[:4], value[-4:], len(value))


def diagnose():
    """Print what this process actually sees for the LLM config, then make
    one real API call and show Mistral's raw response - for verifying a
    deploy's .env without ever exposing the real key. See watch.py
    --diagnose-llm. Never send this output anywhere but your own terminal -
    the masked key is safe to look at, but no need to paste it elsewhere.

    Note: .env only fills in names the environment doesn't already have
    (see config.py's loader, `os.environ.setdefault`) - if MISTRAL_API_KEY
    is also exported some other way (shell profile, systemd Environment=,
    ...), THAT wins over .env silently. Confirmed the shipped
    deploy/watcher.service has no such override as of 25 Sep 2026, but a
    hand-edited unit or shell profile could still do this.
    """
    print("config.DOTENV: %s" % DOTENV)
    print("  exists: %s" % os.path.exists(DOTENV))
    key = os.environ.get("MISTRAL_API_KEY")
    print("MISTRAL_API_KEY: %s" % _masked(key))
    print("model chain (tried in this order): %s" % " -> ".join(_models()))
    print("API endpoint: %s" % API)
    print("TYPESAFE_API_KEY (Jev routing): %s" % _masked(os.environ.get("TYPESAFE_API_KEY")))
    print("JEV_ROUTING: %s  (simple -> %s, complex -> %s)"
          % (jev.mode() if jev.available() or os.environ.get("TYPESAFE_API_KEY") else "inactive - no key",
             JEV_SIMPLE_MODEL, JEV_COMPLEX_MODEL))
    if not key:
        print("\nNo key at all - available() is False, the bot is running "
              "keyword-only. Nothing further to test.")
        return

    print("\nTesting each model in the chain with one real call...")
    working = []
    for model in _models():
        try:
            r = requests.post(API, json={"model": model, "max_tokens": 5,
                                         "messages": [{"role": "user", "content": "say hi"}]},
                              timeout=30, headers={"Authorization": "Bearer %s" % key})
        except requests.RequestException as e:
            print("  %-24s request failed outright: %s" % (model, e))
            print("  -> this host may not reach api.mistral.ai at all (DNS/firewall/"
                  "outbound block) - try: curl -I https://api.mistral.ai")
            return
        note = {200: "works", 401: "KEY REJECTED - regenerate it on console.mistral.ai",
                403: "not available on this plan/tier", 404: "model id not found",
                429: "throttled (free-tier limit for this model)"}.get(r.status_code, "unexpected")
        print("  %-24s HTTP %s  %s" % (model, r.status_code, note))
        if r.status_code == 200:
            working.append(model)
        elif r.status_code == 401:
            break                               # the key is bad for every model
        time.sleep(1.5)                         # stay under the 1 req/s free-tier limits
    if working:
        print("\n-> OK: the bot will answer using %s." % working[0])
    else:
        print("\n-> No model in the chain works right now - the bot falls back to keyword commands only.")


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
