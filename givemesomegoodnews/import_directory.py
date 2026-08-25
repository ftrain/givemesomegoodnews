"""Turn the Media and Democracy Project directory CSV into catalog entries.

Writes data/orgs_directory.yaml, which seed.py loads alongside the
hand-curated data/orgs.yaml. Keeping them in separate files means a re-import
never clobbers hand-written entries, and the curated file stays readable.

Choices made here, all reversible by re-running:

* Large Chain rows are dropped — the catalog is explicitly about newsrooms
  that are not owned by chains.
* The Center Square and Public News Service are dropped despite their
  Nonprofit label: they are national wire services syndicating into local
  outlets, not local newsrooms.
* Rows whose "website" is a Facebook or Issuu page are dropped; there is
  nothing there to crawl or link to.
* One entry per website. Where several outlets share a site (newspaper
  groups like Colorado Community Media), the first is kept and the rest are
  written to data/sources/folded_outlets.txt for later curation by hand.

Run: python3 -m givemesomegoodnews.import_directory data/sources/<file>.csv
"""

import collections
import csv
import re
import sys

import yaml

from . import config
from .geocode import clean, lookup

KEEP_FUNDING = {"Nonprofit", "Native", "College-based", "Independent"}
DROP_HOSTS = {
    "thecentersquare.com", "publicnewsservice.org", "app.publicnewsservice.org",
    "facebook.com", "www.facebook.com", "issuu.com", "m.facebook.com",
}
MODEL = {
    "Nonprofit": "nonprofit",
    "Native": "Native-owned",
    "College-based": "college-based",
    "Independent": "independently owned",
}
STATES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "Puerto Rico": "PR", "Guam": "GU", "U.S. Virgin Islands": "VI",
    "American Samoa": "AS", "Northern Mariana Islands": "MP",
}
PRECISION_COVERAGE = {"place": "city", "county": "regional", "state": "state"}

# The directory's Features column mixes three different things: who a
# newsroom is owned by or serves, what associations it belongs to, and — in
# a few rows — its beat. Only the first two are features of the newsroom;
# beats already have their own taxonomy in subjects.py, so they are dropped.
FEATURE_CANON = {
    "black-owned": "Black-owned", "black owned": "Black-owned",
    "black voice": "Black audience", "black audience": "Black audience",
    "spanish": "Spanish", "spanish & english": "Spanish",
    "latino": "Latino", "chinese": "Chinese", "french": "French",
    "vietnamese": "Vietnamese", "korean": "Korean", "arabic": "Arabic",
    "hmong": "Hmong", "hmong community": "Hmong",
    "lgbtq+": "LGBTQ+", "lgbtq": "LGBTQ+",
    "native": "Native-owned", "indigenous": "Native-owned",
    "inst. for nonprofit news (inn) member": "INN member",
    "inn member": "INN member", "inn": "INN member",
    "alliance of nonprofit news outlets (anno) member": "ANNO member",
    "anno member": "ANNO member",
    "solutions journalism": "Solutions journalism",
    "documenting local government": "Documenting local government",
    "documenters network": "Documenters Network",
    "street paper": "Street paper",
    "rural": "Rural", "print only": "Print only",
    "employee-owned": "Employee-owned", "worker-owned": "Worker-owned",
    "substack": "Newsletter-native",
    "covering climate now partner": "Covering Climate Now",
}


def normalize_features(raw):
    """Canonical feature tags, dropping beats and one-off editorial notes."""
    out = []
    for part in re.split(r"[;,/]| & ", raw or ""):
        key = part.strip().lower().rstrip(".")
        canon = FEATURE_CANON.get(key)
        if canon and canon not in out:
            out.append(canon)
    return out


def host_of(url):
    m = re.match(r"https?://(?:www\.)?([^/]+)", (url or "").strip())
    return m.group(1).lower() if m else ""


def slugify(name, taken):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60] or "outlet"
    candidate, n = slug, 2
    while candidate in taken:
        candidate, n = f"{slug}-{n}", n + 1
    taken.add(candidate)
    return candidate


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else str(
        config.DATA_DIR / "sources" / "mdp_local_data_2026-06-04.csv"
    )
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    curated = yaml.safe_load(open(config.ORGS_FILE))
    curated_hosts = {host_of(o["url"]) for o in curated}
    curated_slugs = {o["slug"] for o in curated}

    kept, folded, stats = {}, [], collections.Counter()
    for row in rows:
        funding = (row.get("Funding") or "").strip()
        host = host_of(row.get("Website"))
        name = (row.get("Outlet") or "").strip()
        stats["rows"] += 1
        if funding not in KEEP_FUNDING:
            stats[f"skip:{funding or 'blank'}"] += 1
            continue
        if not host or host in DROP_HOSTS:
            stats["skip:no usable website"] += 1
            continue
        if host in curated_hosts:
            stats["skip:already curated"] += 1
            continue
        if not name:
            stats["skip:no name"] += 1
            continue
        if host in kept:
            folded.append(f"{host}\t{name}\t{row.get('State','')}")
            stats["folded into a sibling"] += 1
            continue
        kept[host] = row

    taken = set(curated_slugs)
    out = []
    for host, row in kept.items():
        name = (row["Outlet"] or "").strip()
        state = STATES.get((row.get("State") or "").strip())
        community = clean(row.get("Community"))
        lat = lon = precision = None
        if state:
            lat, lon, precision = lookup(row.get("Community"), state)
        entry = {
            "slug": slugify(name, taken),
            "name": name,
            "url": row["Website"].strip(),
            "source": "mdp",
        }
        if state:
            entry["state"] = state
        if community:
            entry["city"] = community
        if lat is not None:
            entry["lat"] = round(lat, 4)
            entry["lon"] = round(lon, 4)
            entry["geo_precision"] = precision
        entry["coverage_type"] = PRECISION_COVERAGE.get(precision, "city")
        entry["model"] = MODEL[(row["Funding"] or "").strip()]
        features = normalize_features(row.get("Features"))
        if (row["Funding"] or "").strip() == "Native" and "Native-owned" not in features:
            features.append("Native-owned")
        if features:
            entry["features"] = features
        out.append(entry)
        stats[f"geo:{precision}"] += 1

    target = config.DATA_DIR / "orgs_directory.yaml"
    with target.open("w") as f:
        f.write("# Generated by givemesomegoodnews.import_directory — do not hand-edit.\n")
        f.write(f"# Source: {path}\n")
        f.write("# Hand-curated entries live in orgs.yaml and always win on slug.\n\n")
        yaml.safe_dump(out, f, sort_keys=False, allow_unicode=True, width=100)

    folded_path = config.DATA_DIR / "sources" / "folded_outlets.txt"
    folded_path.write_text(
        "# Outlets sharing a website with an entry already imported.\n"
        "# host\toutlet\tstate\n" + "\n".join(sorted(folded)) + "\n"
    )

    print(f"wrote {len(out)} entries -> {target}")
    for key, n in stats.most_common():
        print(f"  {n:6d}  {key}")


if __name__ == "__main__":
    main()
