"""What time it was where the story was published.

A story from Signal Cleveland should say when it ran in Cleveland. The zone
comes from the newsroom's state, which is right for all but a handful of
split states; those are listed below and can be corrected per newsroom with
a `timezone:` field in data/orgs.yaml.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Newsrooms with no fixed geography (national outlets) are stamped Eastern,
# which is where most of them keep their desk.
DEFAULT_TZ = "America/New_York"

# Split states are set to the zone holding most of the population, so
# Tennessee is Central (Nashville, Memphis) and Idaho is Mountain (Boise).
# Override per newsroom in orgs.yaml when that is wrong.
STATE_TZ = {
    "AL": "America/Chicago",   "AK": "America/Anchorage", "AZ": "America/Phoenix",
    "AR": "America/Chicago",   "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York",  "DE": "America/New_York",  "DC": "America/New_York",
    "FL": "America/New_York",  "GA": "America/New_York",  "HI": "Pacific/Honolulu",
    "ID": "America/Boise",     "IL": "America/Chicago",   "IN": "America/Indiana/Indianapolis",
    "IA": "America/Chicago",   "KS": "America/Chicago",   "KY": "America/New_York",
    "LA": "America/Chicago",   "ME": "America/New_York",  "MD": "America/New_York",
    "MA": "America/New_York",  "MI": "America/Detroit",   "MN": "America/Chicago",
    "MS": "America/Chicago",   "MO": "America/Chicago",   "MT": "America/Denver",
    "NE": "America/Chicago",   "NV": "America/Los_Angeles", "NH": "America/New_York",
    "NJ": "America/New_York",  "NM": "America/Denver",    "NY": "America/New_York",
    "NC": "America/New_York",  "ND": "America/Chicago",   "OH": "America/New_York",
    "OK": "America/Chicago",   "OR": "America/Los_Angeles", "PA": "America/New_York",
    "RI": "America/New_York",  "SC": "America/New_York",  "SD": "America/Chicago",
    "TN": "America/Chicago",   "TX": "America/Chicago",   "UT": "America/Denver",
    "VT": "America/New_York",  "VA": "America/New_York",  "WA": "America/Los_Angeles",
    "WV": "America/New_York",  "WI": "America/Chicago",   "WY": "America/Denver",
}

# How recent a story has to be for the time of day to still be news.
RECENT = timedelta(hours=24)

_CACHE = {}


def zone_for(state=None, override=None):
    name = override or STATE_TZ.get((state or "").upper(), DEFAULT_TZ)
    if name not in _CACHE:
        try:
            _CACHE[name] = ZoneInfo(name)
        except Exception:
            _CACHE[name] = ZoneInfo(DEFAULT_TZ)
    return _CACHE[name]


def local_time(when, state=None, override=None):
    """'9:15 AM CDT' in the newsroom's own zone."""
    if not when:
        return ""
    local = when.astimezone(zone_for(state, override))
    return local.strftime("%-I:%M %p %Z").strip()


def local_dateline(when, state=None, override=None, now=None):
    """Short dateline in the newsroom's own zone.

    While a story is still the day's news the hour is part of it, so the
    dateline carries the time and the zone: 'Tue, Aug 25 @ 11AM EDT'. Once
    it is older than a day the hour tells the reader nothing they want and
    the date is the whole fact: 'Tue, Aug 25'.
    """
    if not when:
        return ""
    local = when.astimezone(zone_for(state, override))
    now = now or datetime.now(timezone.utc)
    if now - local < RECENT:
        return local.strftime("%a, %b %-d @ %-I%p %Z").strip()
    return local.strftime("%a, %b %-d").strip()
