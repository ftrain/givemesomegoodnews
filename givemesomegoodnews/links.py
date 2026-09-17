"""Where everything on this site lives, and what a place is called.

One module for the addresses — a subject page, a newsroom, a tag, a cached
picture — so that a page, a card, an RSS item and the search service all
spell them the same way.
"""

import re
from html import escape as esc

from . import images


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "Washington, D.C.", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def place_label(org):
    if org["city"] and org["state"]:
        return f"{org['city']}, {org['state']}"
    if org["state"]:
        return STATE_NAMES.get(org["state"], org["state"])
    return "no fixed geography"


def org_href(org, mode, prefix=""):
    """Internal org page for the site; the org's own site on the one-pager."""
    if mode == "onepage":
        return org["url"]
    return f"{prefix}orgs/{org['slug']}.html"


def feed_page_name(stem, index):
    """feed.html, feed-2.html, feed-3.html ..."""
    return f"{stem}.html" if index == 0 else f"{stem}-{index + 1}.html"


def image_href(name, prefix=""):
    """Where a cached picture is served from: img/<first two of the name>/<name>.
    See givemesomegoodnews.images for why the cache is spread out."""
    return f"{prefix}img/{name[:images.SHARD]}/{name}"


def subject_href(subject, prefix=""):
    return f"{prefix}subjects/{subject.lower().replace(' ', '-')}.html"


def feature_href(feature, prefix=""):
    return f"{prefix}features/{re.sub(r'[^a-z0-9]+', '-', feature.lower()).strip('-')}.html"


def state_href(state_name, prefix=""):
    return f"{prefix}catalog/{re.sub(r'[^a-z0-9]+', '-', state_name.lower()).strip('-')}.html"


def _org_line(art, mode, prefix):
    loc = STATE_NAMES.get(art["state"], art["state"]) if art["state"] else "everywhere"
    href = art["org_url"] if mode == "onepage" else f"{prefix}orgs/{art['slug']}.html"
    return f'<a href="{esc(href)}">{esc(art["org_name"])}</a> ({esc(loc)})'
