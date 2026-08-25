"""Full-text search, served live from Postgres.

The rest of the site is static files, which is why it is cheap to serve and
survives this process being down. Search is the one thing that cannot be
precomputed, so it runs as a small local service behind nginx at /search.

Deliberately not a client-side index: shipping every headline and summary to
every reader would cost more bandwidth than the whole feed does.

Run: python3 -m givemesomegoodnews.searchd [port]
"""

import re
import sys
from html import escape as esc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from . import config, syndicate
from .build_site import (MENU_FEEDS, MENU_SUBJECTS, page, render_feed_item,
                          render_result_map, search_form)
from .db import connect
import collections

from .tags import REGIONS, STATE_REGION as REGIONS_BY_STATE, tag_slug

MAX_RESULTS = 60

SELECT_COLS = """
SELECT a.id, a.url, a.title, a.summary, a.author, a.published_at, a.fetched_at,
       a.image_file, a.image_w, a.image_h, a.image_alt, a.subject,
       o.name, o.slug, o.url, o.support_url, o.support_label,
       o.state, o.city, o.beat, o.coverage, o.coverage_type, o.timezone,
       o.model, o.features, o.feed_url, o.in_default, o.language
FROM articles a JOIN orgs o ON o.id = a.org_id
"""


def build_query(query, tags, region, language, subject):
    """Text search and facets are independent: either alone is a valid ask.

    Browsing by tag with no words typed is the common case for "show me the
    rural papers", so an empty query is not an error — it just orders by
    date instead of relevance.
    """
    where, params = [], []
    if query.strip():
        sql = SELECT_COLS + ", websearch_to_tsquery('english', %s) AS q\n"
        params.append(query)
        where.append("a.search_tsv @@ q")
        order = "ts_rank(a.search_tsv, q) DESC, coalesce(a.published_at, a.fetched_at) DESC"
    else:
        sql = SELECT_COLS
        order = "coalesce(a.published_at, a.fetched_at) DESC"
    for tag in tags:
        where.append("%s = ANY(o.features)")
        params.append(tag)
    if region and region in REGIONS:
        where.append("o.state = ANY(%s)")
        params.append(REGIONS[region])
    if language:
        where.append("o.language = %s")
        params.append(language)
    if subject:
        where.append("a.subject = %s")
        params.append(subject)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT %s"
    params.append(MAX_RESULTS)
    return sql, params

COLS = ("id", "url", "title", "summary", "author", "published_at", "fetched_at",
        "image_file", "image_w", "image_h", "image_alt", "subject", "org_name", "slug",
        "org_url", "support_url", "support_label", "state", "city", "beat",
        "coverage", "coverage_type", "timezone", "model", "features", "org_feed",
        "in_default", "language")

ORG_COLS = ("slug", "name", "lat", "lon", "state")


def load_menu():
    """The menu is built at build time; the service has to look it up."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT subject FROM articles WHERE subject IS NOT NULL ORDER BY 1")
        subjects = [(r[0], re.sub(r"[^a-z0-9]+", "-", r[0].lower()).strip("-"))
                    for r in cur.fetchall()]
    MENU_SUBJECTS[:] = subjects
    MENU_FEEDS[:] = [("feed.xml", "Everything")] + [
        (f"subjects/{slug}.xml", name) for name, slug in subjects
    ]


def facet_bar(query, tags, region, language, rows):
    """Only the tags actually present in these results, each one a toggle.

    Showing the whole taxonomy meant most of it led nowhere from wherever
    you happened to be. What is useful is: here is what this set contains,
    and here is how to drop any of it.
    """
    present = collections.Counter()
    for row in rows:
        for tag in row.get("features") or []:
            present[tag] += 1
        if row.get("state"):
            r = REGIONS_BY_STATE.get(row["state"])
            if r:
                present[r] += 1

    def url_for(drop=None, add=None, drop_region=False, drop_lang=False):
        keep = [t for t in tags if t != drop]
        if add and add not in keep:
            keep = keep + [add]
        params = {"q": query} if query else {}
        if keep:
            params["tag"] = keep
        if region and not drop_region and add not in REGIONS:
            params["region"] = region
        if region and add in REGIONS:
            params["region"] = add
        if language and not drop_lang:
            params["lang"] = language
        return "/search?" + urlencode(params, doseq=True)

    chips = []
    # Active filters first, marked, and clicking one turns it off.
    for tag in tags:
        chips.append(f'<a class="lozenge on" href="{esc(url_for(drop=tag))}" '
                     f'aria-current="page" title="Remove this filter">{esc(tag)} &times;</a>')
    if region:
        chips.append(f'<a class="lozenge on" href="{esc(url_for(drop_region=True))}" '
                     f'aria-current="page" title="Remove this filter">{esc(region)} &times;</a>')
    if language:
        chips.append(f'<a class="lozenge on" href="{esc(url_for(drop_lang=True))}" '
                     f'aria-current="page" title="Remove this filter">{esc(language)} &times;</a>')
    # Then what is left in the results, to narrow further.
    for tag, count in present.most_common(24):
        if tag in tags or tag == region:
            continue
        chips.append(f'<a class="lozenge" href="{esc(url_for(add=tag))}">'
                     f'{esc(tag)} {count}</a>')
    if not chips:
        return ""
    return '<p class="chips">' + "".join(chips) + "</p>"


def feed_link(query, tags, region, language):
    """The RSS equivalent of whatever the reader is currently looking at."""
    params = {k: v for k, v in
              (("q", query), ("tag", list(tags)), ("region", region), ("lang", language)) if v}
    return "search.xml?" + urlencode(params, doseq=True)


def run_search(cur, query, tags, region, language, subject):
    sql, params = build_query(query, tags, region, language, subject)
    cur.execute(sql, params)
    return [dict(zip(COLS, r)) for r in cur.fetchall()]


def render_search_rss(query, tags, region, language, subject):
    label = (query.strip()
             or " + ".join(list(tags) + [x for x in (region, language) if x])
             or "everything")
    with connect() as conn, conn.cursor() as cur:
        rows = run_search(cur, query, tags, region, language, subject)
    return syndicate.render_rss(
        rows, f"{config.SITE_NAME} — {label}",
        f"Search results for {label}, newest first.",
        feed_link(query, tags, region, language), config.SITE_URL.rstrip("/"))


def render(query, tags=(), region="", language="", subject=""):
    tags = list(tags)
    active = [t for t in tags]
    if region:
        active.append(region)
    if language:
        active.append(language)
    heading = "Search"
    if active:
        heading = "Search — " + " + ".join(active)

    with connect() as conn, conn.cursor() as cur:
        parts = [search_form(query)]
        if not query.strip() and not tags and not region and not language:
            parts.append('<p class="meta">Search headlines and summaries, or start '
                         'from a subject or tag in the menu.</p>')
            return page(f"{config.SITE_NAME} — Search", "\n".join(parts))

        rows = run_search(cur, query, tags, region, language, subject)

        if not rows:
            shown = esc(query) if query.strip() else esc(" + ".join(active))
            parts.append(f"<p>Nothing matches <strong>{shown}</strong>.</p>")
        else:
            noun = "story" if len(rows) == 1 else "stories"
            capped = " (strongest matches)" if len(rows) == MAX_RESULTS else ""
            label = esc(query) if query.strip() else esc(" + ".join(active))
            rss = feed_link(query, tags, region, language)
            parts.append(f"<p>{len(rows)} {noun} matching <strong>{label}</strong>{capped}. "
                         f'<a href="{esc(rss)}">Subscribe to this search</a>.</p>')

            # Map first, then the tags in this result set, then the stories.
            seen, result_orgs = set(), []
            for row in rows:
                if row["slug"] in seen:
                    continue
                seen.add(row["slug"])
                cur.execute(
                    "SELECT slug, name, lat, lon, state FROM orgs WHERE slug = %s", (row["slug"],)
                )
                got = cur.fetchone()
                if got:
                    result_orgs.append(dict(zip(ORG_COLS, got)))
            map_svg = render_result_map(result_orgs, caption="Newsrooms in these results")
            if map_svg:
                parts.append(map_svg)
            parts.append(facet_bar(query, tags, region, language, rows))
            parts.append('<div id="feed-items">')
            for row in rows:
                parts.append(render_feed_item(cur, row, with_related=False))
            parts.append("</div>")
        return page(f"{config.SITE_NAME} — {heading}", "\n".join(parts),
                    feed_href=feed_link(query, tags, region, language),
                    feed_title=f"{config.SITE_NAME} — {heading}")


class Handler(BaseHTTPRequestHandler):
    server_version = "givemesomegoodnews-search"

    def do_GET(self):
        parsed = urlparse(self.path)
        wants_rss = parsed.path.rstrip("/") == "/search.xml"
        if not wants_rss and parsed.path.rstrip("/") not in ("/search", ""):
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        query = (params.get("q") or [""])[0][:200]
        tags = [t[:40] for t in params.get("tag", [])][:6]
        region = (params.get("region") or [""])[0][:20]
        language = (params.get("lang") or [""])[0][:20]
        subject = (params.get("subject") or [""])[0][:30]
        try:
            if wants_rss:
                body = render_search_rss(query, tags, region, language, subject).encode("utf-8")
            else:
                body = render(query, tags, region, language, subject).encode("utf-8")
        except Exception:
            self.send_error(500)
            raise
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/rss+xml; charset=utf-8" if wants_rss else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # nginx already logs; don't duplicate


def main():
    load_menu()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
