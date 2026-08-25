"""Load data/orgs.yaml into the orgs table (upsert by slug)."""

import sys

import yaml

from . import config
from .db import connect

FIELDS = [
    "slug", "name", "url", "about_url", "feed_url", "city", "state", "lat",
    "lon", "coverage", "coverage_type", "model", "affiliations", "founded",
    "support_url", "support_label", "beat", "timezone", "tagline", "features", "source", "geo_precision",
]


DIRECTORY_FILE = config.DATA_DIR / "orgs_directory.yaml"


def load_orgs():
    """Hand-curated entries first, then the imported directory.

    A curated slug always wins: orgs.yaml is edited by hand and
    orgs_directory.yaml is regenerated wholesale by the importer.
    """
    with open(config.ORGS_FILE) as f:
        curated = yaml.safe_load(f) or []
    for org in curated:
        org.setdefault("source", "curated")
    imported = []
    if DIRECTORY_FILE.exists():
        with open(DIRECTORY_FILE) as f:
            imported = yaml.safe_load(f) or []
    curated_slugs = {o["slug"] for o in curated}
    curated_urls = {(o.get("url") or "").rstrip("/") for o in curated}
    fresh = [
        o for o in imported
        if o["slug"] not in curated_slugs and (o.get("url") or "").rstrip("/") not in curated_urls
    ]
    print(f"loaded {len(curated)} curated + {len(fresh)} imported "
          f"({len(imported) - len(fresh)} shadowed by curated entries)")
    return curated + fresh


def main():
    orgs = load_orgs()
    seen = set()
    for org in orgs:
        if org["slug"] in seen:
            sys.exit(f"duplicate slug: {org['slug']}")
        seen.add(org["slug"])

    with connect() as conn, conn.cursor() as cur:
        for org in orgs:
            row = {k: org.get(k) for k in FIELDS}
            row["affiliations"] = org.get("affiliations") or []
            row["features"] = org.get("features") or []
            cur.execute(
                """
                INSERT INTO orgs (slug, name, url, about_url, feed_url, city, state,
                                  lat, lon, coverage, coverage_type, model, affiliations, founded,
                                  support_url, support_label, beat, timezone, tagline,
                                  features, source, geo_precision)
                VALUES (%(slug)s, %(name)s, %(url)s, %(about_url)s, %(feed_url)s, %(city)s,
                        %(state)s, %(lat)s, %(lon)s, %(coverage)s, %(coverage_type)s,
                        %(model)s, %(affiliations)s, %(founded)s,
                        %(support_url)s, %(support_label)s, %(beat)s, %(timezone)s, %(tagline)s,
                        %(features)s, %(source)s, %(geo_precision)s)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name, url = EXCLUDED.url,
                    about_url = EXCLUDED.about_url,
                    feed_url = COALESCE(EXCLUDED.feed_url, orgs.feed_url),
                    city = EXCLUDED.city, state = EXCLUDED.state,
                    lat = EXCLUDED.lat, lon = EXCLUDED.lon,
                    coverage = EXCLUDED.coverage,
                    coverage_type = EXCLUDED.coverage_type,
                    model = EXCLUDED.model,
                    affiliations = EXCLUDED.affiliations,
                    founded = EXCLUDED.founded,
                    -- yaml wins when it says something; otherwise keep what we discovered
                    support_url = COALESCE(EXCLUDED.support_url, orgs.support_url),
                    support_label = COALESCE(EXCLUDED.support_label, orgs.support_label),
                    beat = EXCLUDED.beat,
                    timezone = EXCLUDED.timezone,
                    -- a hand-written tagline in yaml wins; else keep what we extracted
                    tagline = COALESCE(EXCLUDED.tagline, orgs.tagline),
                    features = EXCLUDED.features,
                    source = EXCLUDED.source,
                    geo_precision = EXCLUDED.geo_precision
                """,
                row,
            )
        cur.execute("SELECT count(*) FROM orgs")
        print(f"seeded {len(orgs)} orgs; {cur.fetchone()[0]} in database")


if __name__ == "__main__":
    main()
