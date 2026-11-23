"""Explicit calendar dates in free text, parsed in code instead of by the LLM.

"26th and 27th December", "dec 26 & 27", "27 Dec", "December 26 and 27" are
fully determined by the text, but the extraction model dropped them about half
the time (measured 23 Nov 2026: the same message returned dates on one run and
null on the next, and "27th December" alone failed 3/3 on the single-extract
path). A missing date makes the bot ask "which dates?" even though the user
just said, so this fills the gap deterministically. Relative words ("tomorrow",
"this friday") stay with the model, which has the calendar for those.
"""

import datetime as dt
import re

MONTHS = {"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
          "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
          "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
          "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12}
NEXT_YEAR_WITHIN_DAYS = 200
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
# a list of days joined by "," "&" or "and". "to"/"-" are NOT here: "26 to 28" is a
# range, not two dates, and guessing wrong silently watches the wrong days.
_DAYS = r"%s(?:\s*(?:,|&|and)\s*%s)*" % (_DAY, _DAY)
_FIND = re.compile(r"\b(?:(?P<m1>%s)\.?\s+(?P<d1>%s)|(?P<d2>%s)\s+(?:of\s+)?(?P<m2>%s))\b"
                   % (_MON, _DAYS, _DAYS, _MON), re.I)


def passed(text, today):
    """-> the dates written in `text` that are already behind `today` THIS year (what parse() skips, or what a
    past-date message was really about), so the user can be told instead of silently ignored."""
    gone = set()
    for m in _FIND.finditer(text or ""):
        month = MONTHS[(m.group("m1") or m.group("m2")).lower()]
        for day in re.findall(r"\d{1,2}", m.group("d1") or m.group("d2")):
            try:
                d = dt.date(today.year, month, int(day))
            except ValueError:
                continue
            if d < today:
                gone.add(d.isoformat())
    return sorted(gone)


def parse(text, today):
    """-> sorted ['YYYY-MM-DD', ...] for every explicit day+month in `text`.

    `today` is a date. A date that already passed this year means next year - but only when that is
    soon (<= NEXT_YEAR_WITHIN_DAYS): "2 jan" typed on 28 Dec is next week, whereas "3 nov" typed on
    23 Nov would be a year away, which is a slip, not a plan - it is left out so the validator can
    say "that date has already passed" instead of silently watching 2027.
    Impossible dates (31 Feb) are skipped; nothing found -> [].
    """
    found = set()
    for m in _FIND.finditer(text or ""):
        month = MONTHS[(m.group("m1") or m.group("m2")).lower()]
        for day in re.findall(r"\d{1,2}", m.group("d1") or m.group("d2")):
            for year in (today.year, today.year + 1):
                try:
                    d = dt.date(year, month, int(day))
                except ValueError:
                    break
                if d >= today:
                    if (d - today).days <= NEXT_YEAR_WITHIN_DAYS or year == today.year:
                        found.add(d.isoformat())
                    break
    return sorted(found)
