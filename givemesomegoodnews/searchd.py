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
from urllib.parse import parse_qs, urlparse

from . import config
from .build_site import (MENU_FEEDS, MENU_SUBJECTS, page, render_feed_item,
                          render_result_map, search_form)
from .db import connect

MAX_RESULTS = 60

QUERY = """
SELECT a.id, a.url, a.title, a.summary, a.author, a.published_at, a.fetched_at,
       a.image_file, a.image_w, a.image_h, a.image_alt, a.subject,
       o.name, o.slug, o.url, o.support_url, o.support_label,
       o.state, o.city, o.beat, o.coverage, o.coverage_type, o.timezone,
       o.model, o.features, o.feed_url
FROM articles a
JOIN orgs o ON o.id = a.org_id,
     websearch_to_tsquery('english', %s) AS q
WHERE a.search_tsv @@ q
ORDER BY ts_rank(a.search_tsv, q) DESC,
         coalesce(a.published_at, a.fetched_at) DESC
LIMIT %s
"""

COLS = ("id", "url", "title", "summary", "author", "published_at", "fetched_at",
        "image_file", "image_w", "image_h", "image_alt", "subject", "org_name", "slug",
        "org_url", "support_url", "support_label", "state", "city", "beat",
        "coverage", "coverage_type", "timezone", "model", "features", "org_feed")

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


def render(query):
    if not query.strip():
        return page(f"{config.SITE_NAME} — Search",
                    "<h1>Search</h1>\n" + search_form())
    with connect() as conn, conn.cursor() as cur:
        cur.execute(QUERY, (query, MAX_RESULTS))
        rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
        parts = ["<h1>Search</h1>", search_form(query)]
        if not rows:
            parts.append(f"<p>Nothing matches <strong>{esc(query)}</strong>.</p>")
        else:
            noun = "story" if len(rows) == 1 else "stories"
            capped = " (showing the strongest matches)" if len(rows) == MAX_RESULTS else ""
            parts.append(f"<p>{len(rows)} {noun} matching "
                         f"<strong>{esc(query)}</strong>{capped}.</p>")
            # The map goes above the results and the list below it, so the
            # same answer is available spatially and in reading order.
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
        return page(f"{config.SITE_NAME} — {query}", "\n".join(parts))


class Handler(BaseHTTPRequestHandler):
    server_version = "givemesomegoodnews-search"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") not in ("/search", ""):
            self.send_error(404)
            return
        query = (parse_qs(parsed.query).get("q") or [""])[0][:200]
        try:
            body = render(query).encode("utf-8")
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
