"""Seed data/institutions.yaml and fetch each one's About text.

The infrastructure page follows the same rule as the catalog: every
organisation is described in its own words, quoted and linked, never
paraphrased. This is the catalog's fetch_about applied to the funders,
networks and associations rather than to the newsrooms.
"""

import sys
from datetime import datetime, timezone

import yaml

from . import config
from .db import connect, log_fetch
from .extract import paragraphs_from_html
from .fetchutil import about_links_in_html, get
from .taglines import best_sentence

FIELDS = ("slug", "name", "url", "about_url", "kind", "affiliation")


def load_yaml():
    with open(config.DATA_DIR / "institutions.yaml") as f:
        return yaml.safe_load(f)


def fetch_about(inst):
    """Their About page, or the homepage if there isn't a reachable one."""
    for url in (inst.get("about_url"), inst["url"]):
        if not url:
            continue
        try:
            resp = get(url, retries=1)
        except Exception:
            continue
        if resp.status_code != 200:
            continue
        paras = paragraphs_from_html(resp.text)
        if paras:
            return "\n\n".join(paras[:6])[:4000], resp.url
        # Fall back to a linked About page discovered on the homepage.
        for candidate in about_links_in_html(url, resp.text)[:2]:
            try:
                sub = get(candidate, retries=0)
            except Exception:
                continue
            if sub.status_code == 200:
                paras = paragraphs_from_html(sub.text)
                if paras:
                    return "\n\n".join(paras[:6])[:4000], sub.url
    return None, None


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv
    insts = load_yaml()

    with connect() as conn, conn.cursor() as cur:
        for inst in insts:
            row = {k: inst.get(k) for k in FIELDS}
            cur.execute(
                """
                INSERT INTO institutions (slug, name, url, about_url, kind, affiliation)
                VALUES (%(slug)s, %(name)s, %(url)s, %(about_url)s, %(kind)s, %(affiliation)s)
                ON CONFLICT (slug) DO UPDATE SET
                    name = EXCLUDED.name, url = EXCLUDED.url,
                    about_url = EXCLUDED.about_url, kind = EXCLUDED.kind,
                    affiliation = EXCLUDED.affiliation
                """,
                row,
            )

        cur.execute("SELECT id, slug, name, about_text FROM institutions ORDER BY slug")
        rows = cur.fetchall()

    got = 0
    with connect() as conn, conn.cursor() as cur:
        for inst_id, slug, name, existing in rows:
            if only and slug not in only:
                continue
            if existing and not force:
                continue
            spec = next((i for i in insts if i["slug"] == slug), None)
            text, source = fetch_about(spec or {"url": None})
            if text:
                cur.execute(
                    "UPDATE institutions SET about_text = %s, about_source_url = %s, "
                    "tagline = %s, about_fetched_at = %s WHERE id = %s",
                    (text, source, best_sentence(text, name), datetime.now(timezone.utc), inst_id),
                )
                got += 1
            log_fetch(cur, slug, "institution", source or "", bool(text),
                      "ok" if text else "no about text")
            print(f"  {slug}: {'ok' if text else 'blocked or empty'}")
        cur.execute("SELECT count(*) FROM institutions WHERE about_text IS NOT NULL")
        print(f"institutions: {got} fetched this run; {cur.fetchone()[0]} of {len(rows)} described")


if __name__ == "__main__":
    main()
