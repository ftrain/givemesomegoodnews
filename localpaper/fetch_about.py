"""Fetch each org's About page and store its text, in their own words.

Order of preference per org:
  1. data/about_overrides/<slug>.txt — hand-collected text for sites whose
     firewalls block scripted fetches. First lines may be `# source: URL`
     and `# fetched: DATE` comments.
  2. The configured about_url.
  3. Common paths (/about, /about-us, ...) and any link containing "about"
     on the homepage.

Run with --force to refetch orgs that already have about_text.
Run with a list of slugs to limit which orgs are fetched.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

from . import config
from .db import connect, log_fetch
from .extract import paragraphs_from_html
from .fetchutil import get, about_links_in_html

FALLBACK_PATHS = ["about", "about/", "about-us/", "who-we-are/", "about-us", "mission/"]
MAX_STORED_CHARS = 20000
MIN_ACCEPTABLE_CHARS = 250


def _read_override(slug):
    path = config.ABOUT_OVERRIDES_DIR / f"{slug}.txt"
    if not path.exists():
        return None
    source, text_lines = None, []
    for line in path.read_text().splitlines():
        if line.startswith("# source:"):
            source = line.split(":", 1)[1].strip()
        elif line.startswith("# fetched:") or line.startswith("#"):
            continue
        else:
            text_lines.append(line)
    text = "\n\n".join(p.strip() for p in "\n".join(text_lines).split("\n\n") if p.strip())
    return (text, source) if text else None


def _try_fetch(url):
    try:
        resp = get(url, retries=1)
    except Exception as e:
        return None, f"error: {e.__class__.__name__}"
    if resp.status_code != 200:
        return None, f"http {resp.status_code}"
    paras = paragraphs_from_html(resp.text)
    text = "\n\n".join(paras)[:MAX_STORED_CHARS]
    if len(text) < MIN_ACCEPTABLE_CHARS:
        return None, f"only {len(text)} chars extracted"
    return (text, str(resp.url)), "ok"


def fetch_one(org):
    slug, url, about_url = org["slug"], org["url"], org["about_url"]

    override = _read_override(slug)
    if override:
        return slug, override[0], override[1] or about_url or url, "override"

    tried = []
    candidates = []
    if about_url:
        candidates.append(about_url)
    candidates += [urljoin(url, p) for p in FALLBACK_PATHS]

    homepage_html = None
    for cand in candidates:
        result, detail = _try_fetch(cand)
        tried.append(f"{cand} ({detail})")
        if result:
            return slug, result[0], result[1], "fetched"
        time.sleep(0.5)

    # Last resort: scan homepage for an about-ish link we didn't guess.
    try:
        homepage_html = get(url).text
    except Exception:
        homepage_html = None
    if homepage_html:
        for cand in about_links_in_html(url, homepage_html)[:3]:
            if cand in candidates:
                continue
            result, detail = _try_fetch(cand)
            tried.append(f"{cand} ({detail})")
            if result:
                return slug, result[0], result[1], "fetched"

    return slug, None, None, "; ".join(tried)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv

    with connect() as conn, conn.cursor() as cur:
        query = "SELECT slug, url, about_url FROM orgs"
        conds, params = [], []
        if args:
            conds.append("slug = ANY(%s)")
            params.append(args)
        if not force and not args:
            conds.append("about_text IS NULL")
        if conds:
            query += " WHERE " + " AND ".join(conds)
        cur.execute(query + " ORDER BY slug", params)
        orgs = [dict(zip(("slug", "url", "about_url"), r)) for r in cur.fetchall()]

    if not orgs:
        print("nothing to fetch (use --force to refetch)")
        return

    ok = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch_one, orgs))

    with connect() as conn, conn.cursor() as cur:
        for slug, text, source, detail in results:
            if text:
                cur.execute(
                    """UPDATE orgs SET about_text = %s, about_source_url = %s,
                       about_fetched_at = now() WHERE slug = %s""",
                    (text, source, slug),
                )
                log_fetch(cur, slug, "about", source, True, detail)
                print(f"  ok    {slug} ({len(text)} chars, {detail})")
                ok += 1
            else:
                log_fetch(cur, slug, "about", "", False, detail)
                print(f"  FAIL  {slug}: {detail}")
    print(f"about pages: {ok}/{len(orgs)} fetched")


if __name__ == "__main__":
    main()
