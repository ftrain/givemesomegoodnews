"""Which newsrooms belong in the default feed, and how tags group.

The catalog holds nearly two thousand newsrooms, and most of them are
ordinary commercial weeklies. The default feed is the mission- and
community-rooted part of that: nonprofits, co-ops, college and university
papers, Native-owned outlets, the literary magazines, and the Black-owned,
Spanish-language and other community press — which is overwhelmingly
independently owned, and would vanish from the default if ownership alone
decided it.

So the rule is not "not independent". It is "independent *and* carrying no
community or mission tag" that falls out of the default. Everything stays
reachable by tapping a tag.
"""

import re

# Languages other than English, as the directory records them.
LANGUAGE_TAGS = [
    "Spanish", "Chinese", "French", "Korean", "Arabic", "Vietnamese", "Hmong",
]

# Who a newsroom is owned by or answers to.
OWNERSHIP_TAGS = [
    "Nonprofit", "Co-op", "Worker-owned", "Employee-owned", "Journalist-owned",
    "Family-owned", "Native-owned", "University-affiliated", "College",
    "Public media", "Public benefit corp", "Reader-funded", "Newsletter",
    "Newsletter-native", "Independent",
]

# Who a newsroom serves, and how it works.
COMMUNITY_TAGS = [
    "Black-owned", "Black audience", "Latino", "LGBTQ+", "Street paper",
    "Rural", "Literary", "Arts", "Small press",
] + LANGUAGE_TAGS

# Professional associations and practices.
PRACTICE_TAGS = [
    "INN member", "ANNO member", "Solutions journalism",
    "Documenting local government", "Documenters Network",
    "Covering Climate Now", "Print only",
]

TAG_GROUPS = [
    ("Ownership", OWNERSHIP_TAGS),
    ("Community", COMMUNITY_TAGS),
    ("Practice", PRACTICE_TAGS),
]

# A newsroom whose model says only this, and which carries no community or
# mission tag, is an ordinary commercial paper. Still in the catalog, still
# searchable, still one tap away — just not the default view.
_PLAIN_COMMERCIAL = re.compile(r"independent", re.IGNORECASE)

# A model that says any of this is mission-rooted whatever else it says —
# "independent family-owned paper" is the thing this site is about, not an
# ordinary commercial weekly.
_MISSION_MODEL = re.compile(
    r"non-?profit|co-?operative|worker|employee-owned|journalist-|writer-owned|"
    r"famil|public media|public radio|universit|college|native|tribal|"
    r"reader-funded|member-supported|newsletter|benefit corp|civic",
    re.IGNORECASE,
)

_MISSION_TAGS = set(COMMUNITY_TAGS + PRACTICE_TAGS) | {
    "Nonprofit", "Co-op", "Worker-owned", "Employee-owned", "Journalist-owned",
    "Native-owned", "University-affiliated", "College", "Public media",
    "Reader-funded", "Newsletter-native",
}


def in_default_feed(model, features):
    """True when a newsroom belongs in the feed a first-time reader sees."""
    features = set(features or [])
    if features & _MISSION_TAGS:
        return True
    if _MISSION_MODEL.search(model or ""):
        return True
    return not _PLAIN_COMMERCIAL.search(model or "")


def tag_slug(tag):
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


def language_of(features):
    """The language a newsroom publishes in; English unless tagged otherwise."""
    for tag in LANGUAGE_TAGS:
        if tag in (features or []):
            return tag
    return "English"


# Census regions, so "southern newsrooms" is answerable without a tag for it.
REGIONS = {
    "Northeast": ["CT", "ME", "MA", "NH", "RI", "VT", "NJ", "NY", "PA"],
    "Midwest": ["IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"],
    "South": ["DE", "DC", "FL", "GA", "MD", "NC", "SC", "VA", "WV", "AL", "KY",
              "MS", "TN", "AR", "LA", "OK", "TX"],
    "West": ["AZ", "CO", "ID", "MT", "NV", "NM", "UT", "WY", "AK", "CA", "HI", "OR", "WA"],
    "Territories": ["PR", "GU", "VI", "AS", "MP"],
}
STATE_REGION = {st: name for name, states in REGIONS.items() for st in states}


def region_of(state):
    return STATE_REGION.get((state or "").upper())


def all_tags():
    """Every tag that can be filtered on, grouped for display."""
    return TAG_GROUPS + [("Region", list(REGIONS))]
