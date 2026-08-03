"""Sending messages, and reading the ones you type back."""

import time

import requests

from .config import *
from .messages import live_report, pretty_date


def send_telegram(text):
    """Send a message, retrying hard. Never raises.

    Sending used to have no retry at all, so a single connection reset - which
    this network produces on roughly one attempt in six - lost the message and
    threw the exception up into the caller. That cost a reply, and would just as
    easily have cost the one alert this whole thing exists to deliver.

    Returns True if every chunk was accepted.
    """
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("no telegram creds set; message was:\n" + text)
        return False

    url = "https://api.telegram.org/bot%s/sendMessage" % token
    ok = True
    for i in range(0, len(text), 3800):      # telegram caps messages at 4096
        chunk = text[i:i + 3800]
        for attempt in range(5):
            try:
                r = requests.post(url, json={"chat_id": chat, "text": chunk,
                                             "disable_web_page_preview": True},
                                  timeout=20)
                if r.status_code == 200:
                    break
                print("telegram error %s: %s" % (r.status_code, r.text[:200]))
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    ok = False       # malformed or unauthorised: retrying won't help
                    break
            except requests.RequestException as e:
                print("telegram send failed (attempt %d): %s" % (attempt + 1, e))
            time.sleep(2 * (attempt + 1))
        else:
            print("GAVE UP sending a message after 5 attempts")
            ok = False
    return ok


def poll_commands(state, wait=0):
    """Read anything you typed to the bot.

    wait=0 returns immediately with whatever is queued - used by one-shot runs.
    wait=N is a long poll: Telegram holds the connection open until you send
    something or N seconds pass, which is what makes replies in --serve instant
    without a webhook (a webhook would need a public HTTPS host).

    Messages from any chat other than yours are ignored.
    """
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chat = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    if not (token and chat):
        # Returning instantly here once turned --serve into a 100%-CPU spin that
        # silently answered nothing. Say so, and slow down.
        print("no telegram credentials - cannot read your messages "
              "(is .env next to the working directory?)")
        time.sleep(max(wait, 5))
        return []
    # Reading updates gets connection-reset every so often (roughly 1 try in 6
    # here), while sending never does. Without a retry a single reset silently
    # drops your command and you wait 10 minutes for a reply that never comes.
    # POST rather than GET: GET with these params is reset far more often.
    updates = None
    for attempt in range(3):
        try:
            r = requests.post("https://api.telegram.org/bot%s/getUpdates" % token,
                              json={"offset": state.get("tg_offset", 0), "timeout": wait},
                              timeout=wait + 20)
            updates = r.json().get("result", []) if r.status_code == 200 else []
            break
        except (requests.RequestException, ValueError) as e:
            print("could not read telegram commands (attempt %d): %s" % (attempt + 1, e))
            time.sleep(2 * (attempt + 1))
    if updates is None:
        return []

    commands = []
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1      # ack, so it is not replayed
        msg = u.get("message") or u.get("edited_message") or {}
        if str((msg.get("chat") or {}).get("id")) != chat:
            continue                                  # not you - ignore
        text = (msg.get("text") or "").strip().lower().lstrip("/")
        if text:
            commands.append(text.replace("@", " "))
    return commands


def wants_report(text):
    """True if this message is asking how things are going."""
    return any(w in text for w in ASK_WORDS)


def answer(cmd, hits, broken, bookable, tally=None):
    """Reply to one chat message.

    Tries the brain (LLM + watch handling) first, but any failure there must
    still leave you with a working status command, so it falls through to the
    keyword behaviour rather than raising.
    """
    print("command from you: %s" % cmd)
    try:
        from .brain import handle
        reply = handle(cmd, hits, broken, bookable, tally)
        if reply:
            send_telegram(reply)
            return
    except Exception as e:                      # never let chat break the watcher
        print("brain failed, falling back to keywords: %s: %s" % (type(e).__name__, e))

    if wants_report(cmd):
        send_telegram(live_report(hits, broken, bookable,
                                  checks=(tally or {}).get("checks", 1)))
    else:
        send_telegram("\n".join([
            "🤖 I'm watching %s for you." % SITE,
            "",
            "I'm not an AI - I just look for a keyword. Say any of:",
            "  report · status · check · update · news",
            "with or without a /, in any sentence.",
            "  e.g. \"report\", \"/status\", \"any update?\"",
            "",
            "You'll also get a report at the end of each shift,",
            "and one 🚨 alert the moment %s opens for %s."
            % (FORMAT, " & ".join(pretty_date(d) for d in DATES))]))
