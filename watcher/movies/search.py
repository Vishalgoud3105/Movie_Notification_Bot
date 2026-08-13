"""Finding the District page for a title the user named in a message.

The LLM says what the user meant; this decides whether such a thing actually
exists on District. Keeping those separate matters: a model will happily invent
a plausible URL, and the watcher would then poll a 404 forever while looking
perfectly healthy.

Movies only for now. District also has /events (concerts, sports), but those
pages do not use the movieSessions/arrangedSessions structure the reader parses,
so claiming support would be a lie - see `looks_like_event`.
"""

import difflib
import re

import requests

from .config import *
from .district import BROWSER_HEADERS

LISTING = "https://www.district.in/movies"

# /movies/<slug>-movie-tickets[-in-<city>]-MV<id>
LINK = re.compile(r'/movies/([a-z0-9\-]+?)-movie-tickets(?:-in-([a-z0-9\-]+))?-MV(\d+)')

EVENT_WORDS = ("concert", "gig", "tour", "festival", "match", "cricket", "ipl",
               "stand-up", "standup", "comedy show", "sports")


def looks_like_event(title):
    """True if this is probably not a movie, so we can say so honestly."""
    t = (title or "").lower()
    return any(w in t for w in EVENT_WORDS)


def catalogue(session=None):
    """[(movie_id, slug, {city: url})] for everything District lists today."""
    get = (session or requests).get
    try:
        html = get(LISTING, headers=BROWSER_HEADERS, timeout=30).text
    except requests.RequestException as e:
        print("could not load the District listing: %s" % e)
        return []

    found = {}
    for slug, city, mv in LINK.findall(html):
        entry = found.setdefault(mv, {"slug": slug, "cities": {}})
        if city:
            entry["cities"][city] = (
                "https://www.district.in/movies/%s-movie-tickets-in-%s-MV%s"
                % (slug, city, mv))
        else:
            entry["cities"].setdefault("_default",
                                       "https://www.district.in/movies/%s-movie-tickets-MV%s"
                                       % (slug, mv))
    return [(mv, e["slug"], e["cities"]) for mv, e in found.items()]


def find(title, city=None, session=None):
    """Best match for a title -> {'title','url','city','movie_id'} or None.

    Matches on the slug rather than anything the model produced, so a
    hallucinated film simply finds nothing instead of becoming a dead URL.
    """
    if not title:
        return None
    items = catalogue(session)
    if not items:
        return None

    want = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slugs = {slug: (mv, cities) for mv, slug, cities in items}

    # exact, then containment, then containment ignoring separators, then fuzzy
    # - in descending order of confidence. The separator-blind pass is what lets
    # "spiderman" find "spider-man-brand-new-day", which is how people type it.
    flat = lambda s: s.replace("-", "")
    best = None
    if want in slugs:
        best = want
    if not best:
        contained = [s for s in slugs if want in s or s in want]
        best = max(contained, key=len) if contained else None
    if not best:
        contained = [s for s in slugs
                     if flat(want) in flat(s) or flat(s) in flat(want)]
        best = min(contained, key=len) if contained else None
    if not best:
        close = difflib.get_close_matches(want, list(slugs), n=1, cutoff=0.6)
        best = close[0] if close else None
    if not best:
        return None

    mv, cities = slugs[best]
    key = (city or "").lower().replace(" ", "-") or None
    url = (cities.get(key) or cities.get("_default")
           or next(iter(cities.values()), None))
    if key and key not in cities:
        print("District has no %s page for %s; using %s"
              % (key, best, "the default city page" if "_default" in cities
                 else list(cities)[0]))
    return {"title": best.replace("-", " ").title(), "url": url,
            "city": key if key in cities else None, "movie_id": mv,
            "cities": sorted(c for c in cities if c != "_default")}
