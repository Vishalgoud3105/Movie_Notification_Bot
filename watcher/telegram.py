"""Sending messages, and reading the ones you type back.

Pure transport, shared by every domain - no domain knows about movies, buses
or anything else. Dispatching a reply to the right domain's brain is
watcher/router.py's job, not this module's.

Broadcasts to multiple chats at once: the owner's personal DM (from
TELEGRAM_CHAT_ID in .env, permanent, never removable) plus any group chats
the owner has since added the bot to. Commands are accepted from any of them
and route to the same shared watch - this is meant for "watch this with me",
not per-chat isolated watches.
"""

import json
import os
import time

import requests

from .config import *

KNOWN_CHATS_FILE = os.environ.get("KNOWN_CHATS_FILE", "known_chats.json")


def _owner_id():
    """The owner's own chat, fixed anchor from .env - it can never be dropped
    via the discovered-chats file, and it is the only identity allowed to add
    a new group (see _chat_membership_events). Without that check, anyone who
    finds the bot's username could add it to their own group and silently
    redirect every alert away from the owner.

    Read fresh each call, not cached at import time: .env is normally static
    for a process's whole lifetime so this makes no practical difference in
    production, but it does mean tests can reassign os.environ and have it
    take effect immediately, same pattern used everywhere else in this repo.
    """
    return os.environ.get("TELEGRAM_CHAT_ID")


def _load_known_chats():
    """The owner's DM plus every group discovered so far."""
    owner = _owner_id()
    chats = {str(owner)} if owner else set()
    if os.path.exists(KNOWN_CHATS_FILE):
        try:
            with open(KNOWN_CHATS_FILE) as f:
                chats |= {str(c) for c in json.load(f).get("chats", [])}
        except (ValueError, OSError):
            pass
    return chats


def _save_extra_chats(chats):
    """Persists everything except the owner's own chat - that always comes
    fresh from .env, so a corrupted or hand-edited file can never lose it."""
    owner = str(_owner_id())
    tmp = KNOWN_CHATS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"chats": sorted(c for c in chats if c != owner)}, f)
    os.replace(tmp, KNOWN_CHATS_FILE)


def _chat_membership_events(updates):
    """(joined: list[chat_id], left: list[chat_id]) from this update batch.

    `joined` only includes groups added by the OWNER (see module docstring) -
    a group added by anyone else is logged and ignored, never watched.
    `left` needs no such check: Telegram itself reporting the bot lost access
    is authoritative regardless of who removed it, so it is always honoured.
    """
    joined, left = [], []
    for u in updates:
        m = u.get("my_chat_member")
        if not m:
            continue
        chat = m.get("chat") or {}
        if chat.get("type") not in ("group", "supergroup"):
            continue                      # the owner's own DM is already known
        status = (m.get("new_chat_member") or {}).get("status")
        if status in ("member", "administrator"):
            actor = str((m.get("from") or {}).get("id"))
            owner = _owner_id()
            if owner and actor == str(owner):
                joined.append(chat.get("id"))
            else:
                print("ignored a group add by someone other than the owner (id %s)" % actor)
        elif status in ("left", "kicked"):
            left.append(chat.get("id"))
    return joined, left


def _send_one(token, chat, text):
    """Send (possibly chunked) text to exactly one chat. Never raises.

    Sending used to have no retry at all, so a single connection reset - which
    this network produces on roughly one attempt in six - lost the message and
    threw the exception up into the caller. Returns True if every chunk sent.
    """
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
                print("telegram error %s (chat %s): %s" % (r.status_code, chat, r.text[:200]))
                if r.status_code == 403:
                    # the bot was removed/blocked here - stop trying this chat.
                    # A no-op for the owner's own chat: it is always re-derived
                    # from .env on the next load, never actually lost.
                    chats = _load_known_chats()
                    chats.discard(str(chat))
                    _save_extra_chats(chats)
                if 400 <= r.status_code < 500 and r.status_code != 429:
                    ok = False       # malformed or unauthorised: retrying won't help
                    break
            except requests.RequestException as e:
                print("telegram send failed (chat %s, attempt %d): %s" % (chat, attempt + 1, e))
            time.sleep(2 * (attempt + 1))
        else:
            print("GAVE UP sending to chat %s after 5 attempts" % chat)
            ok = False
    return ok


def send_telegram(text):
    """Broadcast to every known chat. Returns True only if every chat got it."""
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chats = _load_known_chats()
    if not (token and chats):
        print("no telegram creds/chats set; message was:\n" + text)
        return False
    # a list, not a generator: all() must not short-circuit and skip chats
    return all([_send_one(token, chat, text) for chat in chats])


def poll_commands(state, wait=0):
    """Read anything typed to the bot, from any known chat.

    wait=0 returns immediately with whatever is queued - used by one-shot runs.
    wait=N is a long poll: Telegram holds the connection open until you send
    something or N seconds pass, which is what makes replies in --serve instant
    without a webhook (a webhook would need a public HTTPS host).

    Also watches this same update batch for the owner adding/losing a group -
    see _chat_membership_events() - since that is exactly the same feed this
    already has to read every cycle regardless.
    """
    token = os.environ.get("TELEGRAM_API_TOKEN")
    chats = _load_known_chats()
    if not (token and chats):
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

    joined, left = _chat_membership_events(updates)
    if joined or left:
        current = _load_known_chats()
        for c in joined:
            if str(c) not in current:
                current.add(str(c))
                print("owner added me to a new group (%s) - watching from there too" % c)
                from . import onboarding
                _send_one(token, str(c), onboarding.welcome_message())
        for c in left:
            current.discard(str(c))
        _save_extra_chats(current)
        chats = current

    commands = []
    for u in updates:
        state["tg_offset"] = u["update_id"] + 1      # ack, so it is not replayed
        msg = u.get("message") or u.get("edited_message") or {}
        if str((msg.get("chat") or {}).get("id")) not in chats:
            continue                                  # not a known chat - ignore
        text = (msg.get("text") or "").strip().lower().lstrip("/")
        if text:
            commands.append(text.replace("@", " "))
    return commands


def wants_report(text):
    """True if this message is asking how things are going."""
    return any(w in text for w in ASK_WORDS)
