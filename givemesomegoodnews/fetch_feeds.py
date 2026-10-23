"""Crawl every org's RSS/Atom feed and store new articles with embeddings.

Feed URL resolution per org: use orgs.feed_url if set; otherwise discover
via <link rel="alternate"> on the homepage, then try common feed paths.
Discovered URLs are saved back to the orgs table so the next crawl is
direct. Articles are keyed by canonical URL, so re-running is idempotent.
"""

import re
import sys
from html import unescape
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import feedparser

from . import config
from .db import connect, log_fetch, vec_literal
from .embedder import get_embedder
from .images import cache_image
from .language import detect as detect_language
from .subjects import classify
from .extract import clean_summary, text_from_html_fragment
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


_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ALT_RE = re.compile(r"""\balt\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)


def _entry_image(entry, link):
    """(url, alt) for the best image a feed entry offers.

    Alt text is kept as the publisher wrote it — a screen reader should hear
    their caption, not our guess. Returns (None, None) when there is none.
    """
    for mc in entry.get("media_content") or []:
        url = mc.get("url")
        if url and str(mc.get("medium") or "image") == "image":
            return urljoin(link, url), None
    for mt in entry.get("media_thumbnail") or []:
        if mt.get("url"):
            return urljoin(link, mt["url"]), None
    for enc in entry.get("enclosures") or []:
        if str(enc.get("type") or "").startswith("image/") and enc.get("href"):
            return urljoin(link, enc["href"]), None
    # Otherwise take the first image out of the entry body, with its alt.
    html = "".join((c.get("value") or "") for c in (entry.get("content") or []))
    html += entry.get("summary") or ""
    tag = _IMG_TAG_RE.search(html)
    if tag:
        src = _IMG_SRC_RE.search(tag.group(0))
        if src:
            url = (src.group(2) or src.group(3) or src.group(4) or "").strip()
            alt_m = _ALT_RE.search(tag.group(0))
            alt = (alt_m.group(2) or alt_m.group(3) or alt_m.group(4) or "").strip() if alt_m else None
            if url:
                return urljoin(link, url), (unescape(alt)[:300] if alt else None)
    return None, None


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


# Some CMSes stamp a story in local time but label it UTC, which lands it
# hours in the future — and a future date pins a story to the top of a
# reverse-chronological feed indefinitely. Anything beyond this much grace
# is treated as unusable, and the fetch time stands in for it.
FUTURE_GRACE = timedelta(hours=2)


# How far back --backfill will page through one feed. A WordPress feed holds
# ten stories a page, so this is a busy newsroom's month or a small one's year;
# the date is what actually stops the paging, this is only the ceiling.
BACKFILL_MAX_PAGES = 50


def _oldest(parsed):
    times = [t for t in (_entry_time(e) for e in parsed.entries) if t]
    return min(times) if times else None


def _next_page(feed_url, parsed, n):
    """Where page n of this feed should be, or None where it has no pages.

    A feed that follows RFC 5005 names its next page itself. Otherwise the
    WordPress convention, ?paged=N, which most local newsrooms' feeds answer;
    a site that ignores the parameter just serves its front page again, and
    the caller stops when a page brings nothing new.
    """
    for link in parsed.feed.get("links", []) or []:
        if link.get("rel") == "next" and link.get("href"):
            return urljoin(feed_url, link["href"])
    u = urlsplit(feed_url)
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k != "paged"]
    q.append(("paged", str(n)))
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), ""))


def _page_back(feed_url, parsed, until):
    """Page a feed back until its entries are older than `until`.

    Returns the entries of every page, newest page first. Stops at the first
    page that is missing, that brings no link the earlier pages lacked (the
    site ignored the page number), or whose oldest story is already past
    `until`; and at BACKFILL_MAX_PAGES regardless.
    """
    entries = list(parsed.entries)
    seen = {e.get("link") for e in parsed.entries}
    page = parsed
    for n in range(2, BACKFILL_MAX_PAGES + 1):
        oldest = _oldest(page)
        if oldest and oldest < until:
            break
        url = _next_page(feed_url, page, n)
        if not url:
            break
        time.sleep(0.5)
        page = _fetch_feed(url)
        if not page:
            break
        links = {e.get("link") for e in page.entries}
        if not links - seen:
            break
        seen |= links
        entries.extend(page.entries)
    return entries


def crawl_one(org, until=None):
    # A deploy is waiting for this slice's lock. The feeds already in flight
    # finish — they are a timeout away at worst — and the rest of the queue
    # drains in a moment, which is the difference between a deploy waiting
    # five minutes and waiting thirty seconds. Nothing is lost: the rotation
    # is ordered by when a feed was last crawled, so a feed passed over here
    # is at the front of the next slice, five minutes later.
    if config.standing_down():
        return org, None, None
    feed_url, parsed = resolve_feed(org)
    if not parsed:
        return org, None, []

    cutoff = datetime.now(timezone.utc) - timedelta(days=config.MAX_ARTICLE_AGE_DAYS)
    if until:
        entries = _page_back(feed_url, parsed, max(until, cutoff))
    else:
        entries = parsed.entries[: config.MAX_ENTRIES_PER_FEED]
    items = []
    for entry in entries:
        link = entry.get("link")
        title = (entry.get("title") or "").strip()
        if not link or not title:
            continue
        if any(pat in link for pat in config.EXCLUDE_URL_SUBSTRINGS):
            continue
        # Some feeds emit a bare homepage with the headline stuffed into the
        # query string. That is not a story link.
        if not urlsplit(link).path.strip("/"):
            continue
        published = _entry_time(entry)
        if published and published < cutoff:
            continue
        if published and published > datetime.now(timezone.utc) + FUTURE_GRACE:
            published = None
        summary = clean_summary(text_from_html_fragment(entry.get("summary", "") or ""))[:1500]
        author = (entry.get("author") or "").strip()[:200] or None
        categories = _entry_categories(entry)
        image_url, image_alt = _entry_image(entry, link)
        url = canonical_url(link)
        subject, subject_source = classify(categories, url)
        items.append(
            {
                "url": url,
                "title": title[:500],
                "summary": summary,
                "author": author,
                "published_at": published,
                "image_url": image_url,
                "image_alt": image_alt,
                "categories": categories,
                "subject": subject,
                "subject_source": subject_source,
                "language": detect_language(title, summary,
                                            default=org.get("language") or "English"),
            }
        )
    return org, feed_url, items


def main():
    # --rotate N: take the N feeds checked longest ago. Running a slice every
    # few minutes keeps the feed fresh without hitting a thousand publishers
    # at once, and spreads the load rather than spiking it every few hours.
    # N is a value, not a slug — parse it out before reading positional args.
    #
    # --backfill YYYY-MM-DD: page every feed back until its stories are older
    # than that date, for a gap in the archive — a box rebuilt from an old
    # backup. Every feed's front page is crawled as usual, then its second,
    # third... page (RFC 5005 next links, or WordPress's ?paged=N) until the
    # date is reached, the site stops answering, or it repeats itself.
    args = sys.argv[1:]
    rotate = None
    until = None
    if "--rotate" in args:
        i = args.index("--rotate")
        rotate = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    if "--backfill" in args:
        i = args.index("--backfill")
        until = datetime.fromisoformat(args[i + 1]).replace(tzinfo=timezone.utc)
        args = args[:i] + args[i + 2:]
    slugs = [a for a in args if not a.startswith("-")]
    embedder = get_embedder()

    with connect() as conn, conn.cursor() as cur:
        query = "SELECT id, slug, url, feed_url, language FROM orgs WHERE crawl_feed"
        params = []
        if slugs:
            query += " AND slug = ANY(%s)"
            params.append(slugs)
        if rotate:
            query += " ORDER BY last_crawled_at NULLS FIRST, id LIMIT %s"
            params.append(rotate)
        else:
            query += " ORDER BY slug"
        cur.execute(query, params)
        orgs = [dict(zip(("id", "slug", "url", "feed_url", "language"), r))
                for r in cur.fetchall()]

    with ThreadPoolExecutor(max_workers=config.CRAWL_WORKERS) as pool:
        results = list(pool.map(partial(crawl_one, until=until), orgs))

    total_new, no_feed, stood_down = 0, [], 0
    with connect() as conn, conn.cursor() as cur:
        for org, feed_url, items in results:
            if items is None:
                # Passed over for a deploy: not crawled, not stamped, not
                # logged as a failure. It is simply still waiting its turn.
                stood_down += 1
                continue
            if not feed_url:
                no_feed.append(org["slug"])
                # Stamp it anyway so a feedless org doesn't monopolise the
                # front of the rotation queue forever.
                cur.execute("UPDATE orgs SET last_crawled_at = now() WHERE id = %s", (org["id"],))
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
                with ThreadPoolExecutor(max_workers=config.CRAWL_WORKERS) as img_pool:
                    cached = list(img_pool.map(cache_image, [i["image_url"] for i in new_items]))
                for item, (image_file, image_w, image_h) in zip(new_items, cached):
                    item["image_file"], item["image_w"], item["image_h"] = image_file, image_w, image_h

                vecs = embedder.embed([f"{i['title']} {i['summary']}" for i in new_items])
                for item, vec in zip(new_items, vecs):
                    cur.execute(
                        """INSERT INTO articles (org_id, url, title, summary, author, published_at,
                                                 image_url, image_alt, image_file, image_w, image_h,
                                                 categories, subject, subject_source, language, embedding)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (url) DO NOTHING""",
                        (org["id"], item["url"], item["title"], item["summary"],
                         item["author"], item["published_at"], item["image_url"], item["image_alt"],
                         item["image_file"], item["image_w"], item["image_h"],
                         item["categories"], item["subject"],
                         item["subject_source"], item["language"], vec_literal(vec)),
                    )
            cur.execute("UPDATE orgs SET last_crawled_at = now() WHERE id = %s", (org["id"],))
            log_fetch(cur, org["slug"], "feed", feed_url, True, f"{len(new_items)} new / {len(items)} in feed")
            print(f"  {org['slug']}: {len(new_items)} new / {len(items)} in feed")
            total_new += len(new_items)

    print(f"feeds: {total_new} new articles; no feed for: {', '.join(no_feed) or 'none'}")
    if stood_down:
        print(f"feeds: {stood_down} left for the next slice — a deploy asked for the lock")


if __name__ == "__main__":
    main()
