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

from . import config
from .build_site import (MENU_FEEDS, MENU_SUBJECTS, page, render_feed_item,
                          render_result_map, search_form)
from .db import connect
from .tags import REGIONS, TAG_GROUPS, all_tags, tag_slug

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


def facet_bar(query, tags, region, language):
    """Every tag as a lozenge that toggles itself in or out of the query."""
    def link(params, label, active):
        qs = urlencode(params, doseq=True)
        current = ' aria-current="page"' if active else ""
        return f'<a class="lozenge" href="/search?{qs}"{current}>{esc(label)}</a>'

    out = []
    for group, group_tags in all_tags():
        lozenges = []
        for tag in group_tags:
            if group == "Region":
                active = region == tag
                params = {"q": query}
                if tags:
                    params["tag"] = tags
                if language:
                    params["lang"] = language
                if not active:
                    params["region"] = tag
            else:
                active = tag in tags
                remaining = [t for t in tags if t != tag] if active else tags + [tag]
                params = {"q": query}
                if remaining:
                    params["tag"] = remaining
                if region:
                    params["region"] = region
                if language:
                    params["lang"] = language
            lozenges.append(link({k: v for k, v in params.items() if v}, tag, active))
        out.append(f"<h3>{esc(group)}</h3><p>" + "".join(lozenges) + "</p>")
    return "".join(out)


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
        parts = ["<h1>Search</h1>", search_form(query)]
        if not query.strip() and not tags and not region and not language:
            parts.append("<p class=\"meta\">Type something, or tap a tag to browse. "
                         "Tags combine: Rural plus South, or Black-owned plus Nonprofit.</p>")
            parts.append(facet_bar(query, tags, region, language))
            return page(f"{config.SITE_NAME} — Search", "\n".join(parts))

        sql, params = build_query(query, tags, region, language, subject)
        cur.execute(sql, params)
        rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
        parts.append(facet_bar(query, tags, region, language))

        if not rows:
            shown = esc(query) if query.strip() else esc(" + ".join(active))
            parts.append(f"<p>Nothing matches <strong>{shown}</strong>.</p>")
        else:
            noun = "story" if len(rows) == 1 else "stories"
            capped = " (strongest matches)" if len(rows) == MAX_RESULTS else ""
            label = esc(query) if query.strip() else esc(" + ".join(active))
            parts.append(f"<p>{len(rows)} {noun} matching <strong>{label}</strong>{capped}.</p>")

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
            parts.append('<div id="feed-items">')
            for row in rows:
                parts.append(render_feed_item(cur, row, with_related=False))
            parts.append("</div>")
        return page(f"{config.SITE_NAME} — {heading}", "\n".join(parts))


class Handler(BaseHTTPRequestHandler):
    server_version = "givemesomegoodnews-search"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") not in ("/search", ""):
            self.send_error(404)
            return
        params = parse_qs(parsed.query)
        query = (params.get("q") or [""])[0][:200]
        tags = [t[:40] for t in params.get("tag", [])][:6]
        region = (params.get("region") or [""])[0][:20]
        language = (params.get("lang") or [""])[0][:20]
        subject = (params.get("subject") or [""])[0][:30]
        try:
            body = render(query, tags, region, language, subject).encode("utf-8")
        except Exception:
            self.send_error(500)
            raise
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
