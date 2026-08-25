"""Crawl every org's RSS/Atom feed and store new articles with embeddings.

Feed URL resolution per org: use orgs.feed_url if set; otherwise discover
via <link rel="alternate"> on the homepage, then try common feed paths.
Discovered URLs are saved back to the orgs table so the next crawl is
direct. Articles are keyed by canonical URL, so re-running is idempotent.
"""

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser

from . import config
from .db import connect, log_fetch, vec_literal
from .embedder import get_embedder
from .images import cache_image
from .subjects import classify
from .extract import text_from_html_fragment
from .fetchutil import FEED_CANDIDATE_PATHS, canonical_url, feed_links_in_html, get, looks_like_feed


def _parse_feed_bytes(body):
    parsed = feedparser.parse(body)
    return parsed if parsed.entries else None


def _fetch_feed(url):
    try:
        resp = get(url, retries=1)
    except Exception:
        return None
    if resp.status_code != 200 or not looks_like_feed(resp.content):
        return None
    return _parse_feed_bytes(resp.content)


def resolve_feed(org):
    """Return (feed_url, parsed_feed) or (None, None)."""
    if org["feed_url"]:
        parsed = _fetch_feed(org["feed_url"])
        if parsed:
            return org["feed_url"], parsed

    try:
        homepage = get(org["url"], retries=1)
        html = homepage.text if homepage.status_code == 200 else ""
        base = str(homepage.url)
    except Exception:
        html, base = "", org["url"]

    for cand in feed_links_in_html(base, html)[:3]:
        parsed = _fetch_feed(cand)
        if parsed:
            return cand, parsed

    for path in FEED_CANDIDATE_PATHS:
        cand = urljoin(base, path)
        parsed = _fetch_feed(cand)
        if parsed:
            return cand, parsed
        time.sleep(0.3)
    return None, None


def _entry_time(entry):
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


_IMG_SRC_RE = re.compile(r"""<img\b[^>]*\bsrc\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)


def _entry_image(entry, link):
    """Best image URL a feed entry offers, in order of how deliberate it is."""
    for mc in entry.get("media_content") or []:
        url = mc.get("url")
        if url and str(mc.get("medium") or "image") == "image":
            return urljoin(link, url)
    for mt in entry.get("media_thumbnail") or []:
        if mt.get("url"):
            return urljoin(link, mt["url"])
    for enc in entry.get("enclosures") or []:
        if str(enc.get("type") or "").startswith("image/") and enc.get("href"):
            return urljoin(link, enc["href"])
    # Otherwise take the first image out of the entry body.
    html = "".join((c.get("value") or "") for c in (entry.get("content") or []))
    html += entry.get("summary") or ""
    m = _IMG_SRC_RE.search(html)
    if m:
        src = (m.group(2) or m.group(3) or m.group(4) or "").strip()
        if src:
            return urljoin(link, src)
    return None


def _entry_categories(entry):
    """The publisher's own categories, verbatim and de-duplicated."""
    out, seen = [], set()
    for tag in entry.get("tags") or []:
        term = (tag.get("term") or "").strip()
        key = term.lower()
        if term and key not in seen:
            seen.add(key)
            out.append(term[:80])
    return out[:12]


def crawl_one(org):
    feed_url, parsed = resolve_feed(org)
    if not parsed:
        return org, None, []

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_ARTICLE_AGE_DAYS)
    items = []
    for entry in parsed.entries[: config.MAX_ENTRIES_PER_FEED]:
        link = entry.get("link")
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        if any(pat in link for pat in config.EXCLUDE_URL_SUBSTRINGS):
            continue
        published = _entry_time(entry)
        if published and published < cutoff:
            continue
        summary = text_from_html_fragment(entry.get("summary", "") or "")[:1500]
        author = (entry.get("author") or "").strip()[:200] or None
        categories = _entry_categories(entry)
        url = canonical_url(link)
        subject, subject_source = classify(categories, url)
        items.append(
            {
                "url": url,
                "title": title[:500],
                "summary": summary,
                "author": author,
                "published_at": published,
                "image_url": _entry_image(entry, link),
                "categories": categories,
                "subject": subject,
                "subject_source": subject_source,
            }
        )
    return org, feed_url, items


def main():
    slugs = [a for a in sys.argv[1:] if not a.startswith("-")]
    embedder = get_embedder()

    with connect() as conn, conn.cursor() as cur:
        query = "SELECT id, slug, url, feed_url FROM orgs"
        params = []
        if slugs:
            query += " WHERE slug = ANY(%s)"
            params.append(slugs)
        cur.execute(query + " ORDER BY slug", params)
        orgs = [dict(zip(("id", "slug", "url", "feed_url"), r)) for r in cur.fetchall()]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(crawl_one, orgs))

    total_new, no_feed = 0, []
    with connect() as conn, conn.cursor() as cur:
        for org, feed_url, items in results:
            if not feed_url:
                no_feed.append(org["slug"])
                log_fetch(cur, org["slug"], "feed", "", False, "no feed found")
                continue
            if feed_url != org["feed_url"]:
                cur.execute("UPDATE orgs SET feed_url = %s WHERE id = %s", (feed_url, org["id"]))

            new_items = []
            for item in items:
                cur.execute("SELECT 1 FROM articles WHERE url = %s", (item["url"],))
                if not cur.fetchone():
                    new_items.append(item)

            if new_items:
                # Pull every image onto our own disk before we store the row;
                # nothing on this site hotlinks a publisher's server.
                with ThreadPoolExecutor(max_workers=8) as img_pool:
                    files = list(img_pool.map(cache_image, [i["image_url"] for i in new_items]))
                for item, image_file in zip(new_items, files):
                    item["image_file"] = image_file

                vecs = embedder.embed([f"{i['title']} {i['summary']}" for i in new_items])
                for item, vec in zip(new_items, vecs):
                    cur.execute(
                        """INSERT INTO articles (org_id, url, title, summary, author, published_at,
                                                 image_url, image_file, categories, subject,
                                                 subject_source, embedding)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (url) DO NOTHING""",
                        (org["id"], item["url"], item["title"], item["summary"],
                         item["author"], item["published_at"], item["image_url"],
                         item["image_file"], item["categories"], item["subject"],
                         item["subject_source"], vec_literal(vec)),
                    )
            log_fetch(cur, org["slug"], "feed", feed_url, True, f"{len(new_items)} new / {len(items)} in feed")
            print(f"  {org['slug']}: {len(new_items)} new / {len(items)} in feed")
            total_new += len(new_items)

    print(f"feeds: {total_new} new articles; no feed for: {', '.join(no_feed) or 'none'}")


if __name__ == "__main__":
    main()
