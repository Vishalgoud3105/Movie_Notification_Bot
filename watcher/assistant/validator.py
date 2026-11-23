"""Argument validation and normalisation: the trust boundary between LLM output and executors.

Enforced here, never in prompts alone:
  * identity keys (chat, user, id, role...) are rejected outright - the chat comes from the server
  * required fields -> ONE combined clarification question, with what was understood so far in
    front of it ("Got the dates (...). Which movie...?"), not a guess
  * types coerced; enums canonicalised (case / space / - / _ tolerant); a list given for a string
    argument is reduced to one value
  * dates must be real days, and not in the past; times are HH:MM and ordered
(Port of the template's planner/validator.py.)
"""

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

FORBIDDEN_KEYS = frozenset({"chat_id", "chat", "user_id", "user", "owner", "owner_id", "id", "role",
                            "permissions", "is_admin", "active", "watch_id"})
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HORIZON_DAYS = 200      # ticket sales open weeks ahead, not a year: a date past this is a model slip (2027 for 2026)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")


@dataclass
class Validation:
    is_valid: bool
    needs_clarification: bool = False
    arguments: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)
    message: Optional[str] = None
    notes: List[str] = field(default_factory=list)      # things the user gave that were dropped (a past date among good ones)


def _squash(text):
    return re.sub(r"[\s_-]+", "", str(text)).lower()


def canonical_enum(value, allowed):
    for option in allowed or []:
        if str(option).lower() == str(value).lower() or _squash(option) == _squash(value):
            return option
    return None


def _scalar(value):
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return parts[0] if len(parts) == 1 else ", ".join(parts)
    return str(value).strip()


def _as_list(value):
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p for p in re.split(r"\s*(?:,|;|\band\b)\s*", str(value)) if p.strip()]


def _valid_date(text):
    if not _DATE_RE.fullmatch(text or ""):
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:                       # 2026-02-31 is not a day
        return None


def _time(text):
    m = _TIME_RE.fullmatch(str(text or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return "%02d:%s" % (int(m.group(1)), m.group(2))


def validate(capability, raw_args, today=None):
    today = today or dt.date.today()
    raw_args = raw_args or {}
    if any(not isinstance(k, str) or k.lower() in FORBIDDEN_KEYS for k in raw_args):
        return Validation(False, True, message="I can't use chat or account details as filters. Tell me what to watch.")

    props = (capability.args_schema or {}).get("properties", {})
    out, problems = {}, []                   # problems: [(field, message)]
    for name, spec in props.items():
        if name not in raw_args:
            if "default" in spec:
                out[name] = spec["default"]
            continue
        value = raw_args[name]
        if value is None or value == "" or value == []:
            continue
        kind = spec.get("type", "string")
        if kind == "string":
            text = _scalar(value)
            if not text:
                continue
            if name in ("time_from", "time_to"):
                t = _time(text)
                if t is None:
                    continue                 # an unparseable time is dropped, never guessed
                text = t
            elif name == "date":
                d = _valid_date(text)
                if d is None:
                    problems.append((name, "'%s' is not a real calendar date." % text))
                    continue
                if d < today:
                    problems.append((name, "%s has already passed - what date did you mean?" % d.strftime("%A, %d %b")))
                    continue
                if (d - today).days > HORIZON_DAYS:
                    problems.append((name, "%s is too far ahead for ticket sales - what date did you mean?" % d.strftime("%d %b %Y")))
                    continue
            elif spec.get("enum"):
                text = canonical_enum(text, spec["enum"]) or ""
                if not text:
                    continue
            else:
                text = text.lower() if name in ("city", "from_city", "to_city", "seat_category") else text
            out[name] = text
        elif kind == "number":
            try:
                out[name] = float(value) if not isinstance(value, bool) else None
            except (TypeError, ValueError):
                continue
            if out[name] is None or not math.isfinite(out[name]) or out[name] <= 0:   # NaN / Infinity can never match a fare
                out.pop(name, None)
        elif kind == "boolean":
            out[name] = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes")
        elif kind == "array":
            items = _as_list(value)
            if name == "dates":
                kept = []
                for item in items:
                    d = _valid_date(item)
                    if d is None:
                        problems.append((name, "'%s' is not a real calendar date." % item))
                    elif d < today:
                        problems.append((name, "%s has already passed - which dates did you mean?" % d.strftime("%A, %d %b")))
                    elif (d - today).days > HORIZON_DAYS:
                        problems.append((name, "%s is too far ahead for ticket sales - which dates did you mean?" % d.strftime("%d %b %Y")))
                    elif item not in kept:
                        kept.append(item)
                items = kept
            else:
                items = list(dict.fromkeys(i.lower() for i in items))
            if items:
                out[name] = items
        else:
            out[name] = value

    if out.get("time_from") and out.get("time_to") and out["time_from"] > out["time_to"]:
        problems.append(("time_to", "The start time is after the end time - which window did you mean?"))
        out.pop("time_from"), out.pop("time_to")

    changing = bool(capability.change_flag and out.get(capability.change_flag))
    required = [] if changing else list((capability.args_schema or {}).get("required", []))
    missing = [f for f in required if not out.get(f)]
    # a field the user DID give but that was unusable (a past or impossible date) and is now empty must
    # be explained, required or not - otherwise the bot silently asks again and looks like it ignored them
    why = {f: msg for f, msg in problems if not out.get(f)}
    missing += [f for f in why if f not in missing]
    if missing:
        asks = []
        for f in missing:                    # one combined question, no duplicates
            q = why.get(f) or (capability.ask or {}).get(f) or "I need the %s." % f.replace("_", " ")
            if q not in asks:
                asks.append(q)
        got = capability.got(out) if capability.got else ""
        return Validation(False, True, arguments=out, missing=missing, message=(got + " ".join(asks)).strip())
    return Validation(True, arguments=out, notes=[msg for f, msg in problems if out.get(f)])
