"""Resolve "Sitka", "Bledsoe County", "Los Angeles (Greater)" to coordinates.

Offline, from the Census gazetteer in data/us_places.tsv: every incorporated
place, CDP and county, with its internal point. No geocoding API, no key,
no rate limit, and the same answer every run.

Directory city fields are written by hand and look it — trailing periods,
parentheticals, slashes, "Metro", bare county names. clean() strips that
down to something the gazetteer can match, then lookup() tries the place,
then the county, then falls back to the state's centroid.
"""

import csv
import re
from functools import lru_cache

from . import config

# Gazetteer names carry a type suffix: "Abbeville city", "Autauga County".
_SUFFIX = re.compile(
    r"\s+(city|town|village|borough|municipality|CDP|county|parish|"
    r"census area|municipio|city and borough|metro township|urbana)$",
    re.IGNORECASE,
)
_NOISE = re.compile(r"\(.*?\)|\b(greater|metro|metropolitan|area|region|and surrounding)\b",
                    re.IGNORECASE)


def _norm(name):
    name = _SUFFIX.sub("", (name or "").strip())
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()


def clean(community):
    """Best guess at a place name from a directory's free-text city field."""
    text = (community or "").strip()
    if not text or text in {"-", "--", "—"}:
        return ""
    text = _NOISE.sub(" ", text)
    # "Adair County / Creston", "Hulett and Devils Tower" — take the first.
    text = re.split(r"[/;,]| and ", text)[0]
    return re.sub(r"\s+", " ", text).strip(" .")


@lru_cache(maxsize=1)
def _tables():
    places, counties, states = {}, {}, {}
    with open(config.DATA_DIR / "us_places.tsv") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            key = (row["state"], _norm(row["name"]))
            target = counties if row["kind"] == "county" else places
            target.setdefault(key, (float(row["lat"]), float(row["lon"])))
            states.setdefault(row["state"], []).append((float(row["lat"]), float(row["lon"])))
    centroids = {
        st: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
        for st, pts in states.items()
    }
    return places, counties, centroids


def lookup(community, state):
    """(lat, lon, precision) — precision is 'place', 'county' or 'state'."""
    places, counties, centroids = _tables()
    name = _norm(clean(community))
    if name and state:
        if (state, name) in places:
            return (*places[(state, name)], "place")
        if (state, name) in counties:
            return (*counties[(state, name)], "county")
        # "St. Louis" vs "Saint Louis", "Ft." vs "Fort"
        for a, b in (("st ", "saint "), ("ft ", "fort "), ("mt ", "mount ")):
            alt = name.replace(a, b, 1)
            if alt != name and (state, alt) in places:
                return (*places[(state, alt)], "place")
    if state in centroids:
        return (*centroids[state], "state")
    return None, None, None
