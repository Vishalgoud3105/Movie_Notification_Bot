"""Mistral or Groq (LLM_PROVIDER, Mistral by default) for understanding
messages and phrasing replies.

Was Groq until 29 Aug 2026 - switched providers after Groq deprecated
llama-3.3-70b-versatile and its suggested replacement (openai/gpt-oss-120b)
turned out to be a reasoning model, burning tokens on hidden reasoning before
any visible output - see config.py's MISTRAL_MODELS comment and
[[project-bms-gotchas]]. Groq is wired back in as a switchable provider AND
as the automatic failover for Mistral (and vice versa): _call() walks the
active provider's model chain, then the other provider's, so one outage no
longer silences the bot.

Deliberately thin: both providers speak the OpenAI chat-completions shape, so
`requests` is enough and the project keeps its single dependency.

Every entry point degrades to None/False rather than raising. If the LLM is
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

import requests

from .config import *


PROVIDERS = {
    "mistral": {"url": "https://api.mistral.ai/v1/chat/completions", "key": "MISTRAL_API_KEY"},
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions", "key": "GROQ_API_KEY"},
}
# gpt-oss models think before they answer and Groq bills that as completion, so
# a 500-token extract budget can be spent entirely on reasoning (empty JSON).
REASONING_MIN_TOKENS = 1500


def _key(provider):
    return os.environ.get(PROVIDERS[provider]["key"])


def _providers():
    """Providers to try, in order: the active one, then the other as failover.

    Only providers with a key are listed, so a blank key switches that provider
    (and failover to it) off.
    """
    order = [LLM_PROVIDER] + [p for p in PROVIDERS if p != LLM_PROVIDER]
    return [p for p in order if _key(p)]


# Why the last _call() returned None ("rate_limited" | "unavailable"; None after a success), and
# which "provider:model" answered. Read by watcher/assistant/llm_io.py for the user-facing message
# and the per-message trace.
last_failure = None
last_model = None


def available():
    """True if any provider has a key. Never logs or returns the key itself."""
    return bool(_providers())


def _models(provider=None):
    """`provider`'s models (default: the active one), in order, no duplicates."""
    return list(dict.fromkeys(GROQ_MODELS if (provider or LLM_PROVIDER) == "groq" else MISTRAL_MODELS))


def _chain():
    """[(provider, model), ...] in the order _call() tries them: the active provider's models, then the others'."""
    return [(p, m) for p in _providers() for m in _models(p)]


def _fit(max_tokens, messages, provider):
    """Shrink max_tokens so input + reservation fits the provider's TPM ceiling."""
    tpm = GROQ_TOKENS_PER_MINUTE if provider == "groq" else MISTRAL_TOKENS_PER_MINUTE
    used = sum(len(m["content"]) for m in messages) // 4 + 120   # ~4 chars/token + overhead
    return max(200, min(max_tokens, tpm - used))


def _clean(text):
    """Strip a reasoning model's <think> block, which would break JSON/replies."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _call(messages, temperature=0.4, json_mode=False, max_tokens=700):
    """One chat completion, or None on any failure.

    Walks the model chain (see config.py's MISTRAL_MODELS comment): a free-tier
    key can have one model throttled (429) or tier-blocked (403) while another
    works fine, and a single hardcoded model turned that into a total silent
    outage. Those refusals are per-model and won't clear in seconds, so move
    straight to the next model - no sleep. After the active provider's models
    comes the OTHER provider's (see _chain), so a whole-provider outage fails
    over too. A 401 means that provider's KEY is bad, which none of its models
    can fix, so skip the rest of ITS chain only. Only 5xx/network errors are
    worth retrying the same model.
    """
    global last_failure, last_model
    dead, statuses = set(), []
    last_failure = "unavailable"
    for provider, model in _chain():
        if provider in dead:
            continue
        body = {"model": model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
            body["max_tokens"] = max(max_tokens, REASONING_MIN_TOKENS)
        body["max_tokens"] = _fit(body["max_tokens"], messages, provider)
        for attempt in range(3):
            try:
                r = requests.post(PROVIDERS[provider]["url"], json=body, timeout=45,
                                  headers={"Authorization": "Bearer %s" % _key(provider)})
                if r.status_code == 200:
                    text = _clean(r.json()["choices"][0]["message"]["content"])
                    if text:
                        last_failure, last_model = None, "%s:%s" % (provider, model)
                        return text
                    print("%s %s: empty reply - next model" % (provider, model))
                    break
                print("%s HTTP %s (%s): %s" % (provider, r.status_code, model, r.text[:160]))
                statuses.append(r.status_code)
                if r.status_code == 401:
                    dead.add(provider)
                    break
                if r.status_code in (400, 403, 404, 413, 429):
                    break                       # this model won't work now - next one
            except (requests.RequestException, ValueError, KeyError) as e:
                print("%s call failed (%s, attempt %d): %s" % (provider, model, attempt + 1, e))
                statuses.append(0)                 # not an HTTP answer: a timeout among 429s is an outage, not a rate limit
            time.sleep(2 * (attempt + 1))
    # every model answered 429 -> the caller can say "rate limited, wait a minute" instead of "down"
    last_failure = "rate_limited" if statuses and all(c == 429 for c in statuses) else "unavailable"
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
    order = _providers()
    print("LLM_PROVIDER: %s  (failover: %s)" % (
        LLM_PROVIDER, ", ".join(p for p in order if p != LLM_PROVIDER) or "none - other key not set"))
    for p in PROVIDERS:
        print("%s: %s" % (PROVIDERS[p]["key"], _masked(_key(p))))
    print("model chain (tried in this order): %s" % " -> ".join("%s:%s" % pm for pm in _chain()))
    if not order:
        print("\nNo key at all - available() is False, the bot is running "
              "keyword-only. Nothing further to test.")
        return

    print("\nTesting each model in the chain with one real call...")
    working = []
    dead = set()
    for provider, model in _chain():
        if provider in dead:
            continue
        try:
            r = requests.post(PROVIDERS[provider]["url"],
                              json={"model": model,
                                    "max_tokens": REASONING_MIN_TOKENS if "gpt-oss" in model else 5,
                                    "messages": [{"role": "user", "content": "say hi"}]},
                              timeout=30, headers={"Authorization": "Bearer %s" % _key(provider)})
        except requests.RequestException as e:
            print("  %-8s %-24s request failed outright: %s" % (provider, model, e))
            print("  -> this host may not reach %s at all (DNS/firewall/outbound block)"
                  % PROVIDERS[provider]["url"].split("/")[2])
            dead.add(provider)
            continue
        note = {200: "works", 401: "KEY REJECTED - regenerate it in the %s console" % provider,
                403: "not available on this plan/tier", 404: "model id not found",
                413: "request too large for this plan's tokens-per-minute",
                429: "throttled (free-tier limit for this model)"}.get(r.status_code, "unexpected")
        print("  %-8s %-24s HTTP %s  %s" % (provider, model, r.status_code, note))
        if r.status_code == 200:
            working.append("%s:%s" % (provider, model))
        elif r.status_code == 401:
            dead.add(provider)                  # the key is bad for every model of this provider
        time.sleep(1.5)                         # stay under the 1 req/s free-tier limits
    if working:
        print("\n-> OK: the bot will answer using %s." % working[0])
    else:
        print("\n-> No model in the chain works right now - the bot falls back to keyword commands only.")
