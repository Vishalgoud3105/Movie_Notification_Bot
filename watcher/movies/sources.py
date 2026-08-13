"""Picks the ticketing site to read and runs one pass over the watched dates.

Two readers exist because BookMyShow returns 403 to every datacenter IP, so it
only works from a home connection; District answers from anywhere. SOURCE picks
between them and both return the same shape, so nothing downstream cares.
"""

import random
import time

from .bms import fetch, shows_for
from .config import *


def scan(session):
    """One pass over every watched date, from whichever source is configured.

    Returns ({date: [(key, show)]}, unreachable dates, furthest date on sale).
    """
    from . import district          # imported here: district imports config only

    hits, broken, bookable = {}, [], None
    for i, date_code in enumerate(DATES):
        if i:
            time.sleep(random.uniform(4, 11))  # a person does not fetch 7 dates in 200ms

        if SOURCE == "district":
            data = district.fetch(session, date_code)
            if data is None:
                broken.append(date_code)
                continue
            offered = [d.replace("-", "") for d in district.show_dates(data)]
            found = sorted(district.shows_for(data, date_code))
        else:
            data = fetch(session, date_code)
            if data is None:
                broken.append(date_code)
                continue
            # BMS greys out dates it has not scheduled yet; isDisabled mirrors that.
            offered = [d["DateCode"] for d in data.get("ShowDatesArray", [])
                       if not d.get("isDisabled")]
            found = sorted(shows_for(data, date_code))

        print("%s: on sale up to -> %s" % (date_code, (max(offered) if offered else "none")))
        if offered:
            bookable = max(offered) if not bookable else max(bookable, max(offered))
        print("%s: %d shows in window" % (date_code, len(found)))
        hits[date_code] = found
    return hits, broken, bookable
