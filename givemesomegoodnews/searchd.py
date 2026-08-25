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
from .timezones import local_dateline
from .db import connect
import collections

from .build_site import STATE_NAMES as STATE_NAMES_BY_CODE
from .tags import REGIONS, STATE_REGION as REGIONS_BY_STATE, tag_slug

PAGE_SIZE = 30
# A hard ceiling so a pathological query cannot walk the whole archive.
MAX_PAGES = 40

SELECT_COLS = """
SELECT a.id, a.url, a.title, a.summary, a.author, a.published_at, a.fetched_at,
       a.image_file, a.image_w, a.image_h, a.image_alt, a.subject,
       o.name, o.slug, o.url, o.support_url, o.support_label,
       o.state, o.city, o.beat, o.coverage, o.coverage_type, o.timezone,
       o.model, o.features, o.feed_url, o.in_default, o.language
FROM articles a JOIN orgs o ON o.id = a.org_id
"""


def build_query(query, tags, region, language, subject, limit=PAGE_SIZE, offset=0,
                count_only=False, state="", place="", national=False):
    """Text search and facets are independent: either alone is a valid ask.

    Browsing by tag with no words typed is the common case for "show me the
    rural papers", so an empty query is not an error — it just orders by
    date instead of relevance.
    """
    where, params = [], []
    head = "SELECT count(*)\nFROM articles a JOIN orgs o ON o.id = a.org_id\n" if count_only \
        else SELECT_COLS
    if query.strip():
        sql = head + ", websearch_to_tsquery('english', %s) AS q\n"
        params.append(query)
        where.append("a.search_tsv @@ q")
        order = "coalesce(a.published_at, a.fetched_at) DESC, ts_rank(a.search_tsv, q) DESC"
    else:
        sql = head
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
    if state:
        where.append("o.state = %s")
        params.append(state.upper())
    if place:
        # The line reads "Maryland/Baltimore" or "National/Criminal justice",
        # so the second half is a city for a local paper and a beat for a
        # topic-driven one. Match either.
        where.append("(o.city ILIKE %s OR o.beat ILIKE %s)")
        params.extend([place, place])
    if national:
        where.append("o.coverage_type = 'national'")
    if where:
        sql += " WHERE " + " AND ".join(where)
    if count_only:
        return sql, params
    sql += f" ORDER BY {order} LIMIT %s OFFSET %s"
    params.extend([limit, offset])
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


def filter_params(query, tags, region, language, state="", place="", national=False):
    params = {k: v for k, v in
              (("q", query), ("tag", list(tags)), ("region", region), ("lang", language),
               ("state", state), ("place", place)) if v}
    if national:
        params["national"] = "1"
    return params


def feed_link(query, tags, region, language, state="", place="", national=False):
    """The RSS equivalent of whatever the reader is currently looking at."""
    return "search.xml?" + urlencode(
        filter_params(query, tags, region, language, state, place, national), doseq=True)


def run_search(cur, query, tags, region, language, subject, page_num=1,
               state="", place="", national=False):
    offset = (page_num - 1) * PAGE_SIZE
    sql, params = build_query(query, tags, region, language, subject,
                              limit=PAGE_SIZE, offset=offset,
                              state=state, place=place, national=national)
    cur.execute(sql, params)
    rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
    csql, cparams = build_query(query, tags, region, language, subject, count_only=True,
                                state=state, place=place, national=national)
    cur.execute(csql, cparams)
    return rows, cur.fetchone()[0]


def render_search_rss(query, tags, region, language, subject,
                      state="", place="", national=False):
    label = (query.strip()
             or " + ".join(list(tags) + [x for x in (region, language) if x])
             or "everything")
    with connect() as conn, conn.cursor() as cur:
        rows, _total = run_search(cur, query, tags, region, language, subject,
                                  state=state, place=place, national=national)
    return syndicate.render_rss(
        rows, f"{config.SITE_NAME} — {label}",
        f"Search results for {label}, newest first.",
        feed_link(query, tags, region, language, state, place, national),
        config.SITE_URL.rstrip("/"))


def pager(query, tags, region, language, page_num, pages):
    """Previous and next, and nothing clever."""
    if pages <= 1:
        return ""
    def link(n, label):
        params = {k: v for k, v in
                  (("q", query), ("tag", list(tags)), ("region", region), ("lang", language)) if v}
        if n > 1:
            params["page"] = n
        return f'<a class="lozenge" href="/search?{urlencode(params, doseq=True)}">{label}</a>'
    out = []
    if page_num > 1:
        out.append(link(page_num - 1, "&larr; Newer"))
    if page_num < pages:
        out.append(link(page_num + 1, "Older &rarr;"))
    return '<p class="chips">' + "".join(out) + "</p>"


def render(query, tags=(), region="", language="", subject="", page_num=1,
           state="", place="", national=False):
    tags = list(tags)
    active = [t for t in tags]
    if region:
        active.append(region)
    if language:
        active.append(language)
    for extra in (place, STATE_NAMES_BY_CODE.get(state.upper()) if state else None,
                  "National" if national else None):
        if extra:
            active.append(extra)
    heading = "Search"
    if active:
        heading = "Search — " + " + ".join(active)

    with connect() as conn, conn.cursor() as cur:
        parts = [search_form(query)]
        if not query.strip() and not tags and not region and not language \
                and not state and not place and not national:
            parts.append('<p class="meta">Search headlines and summaries, or start '
                         'from a subject or tag in the menu.</p>')
            return page(f"{config.SITE_NAME} — Search", "\n".join(parts))

        rows, total = run_search(cur, query, tags, region, language, subject, page_num,
                                 state=state, place=place, national=national)

        if not rows:
            shown = esc(query) if query.strip() else esc(" + ".join(active))
            parts.append(f"<p>Nothing matches <strong>{shown}</strong>.</p>")
        else:
            noun = "story" if total == 1 else "stories"
            label = esc(query) if query.strip() else esc(" + ".join(active))
            rss = feed_link(query, tags, region, language)
            pages = max(1, min(MAX_PAGES, -(-total // PAGE_SIZE)))
            shown = f", page {page_num} of {pages}" if pages > 1 else ""
            parts.append(f"<p>{total} {noun} matching <strong>{label}</strong>{shown}. "
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
            # Give the map the stories behind each dot, so a tap can show them.
            by_slug = {}
            for row in rows:
                entry = by_slug.setdefault(row["slug"], {
                    "name": row["org_name"], "site": row["org_url"],
                    "support": row.get("support_url") or "",
                    "supportLabel": row.get("support_label") or "Support",
                    "items": [],
                })
                if len(entry["items"]) < 8:
                    when = local_dateline(row["published_at"] or row["fetched_at"],
                                          row.get("state"), row.get("timezone"))
                    entry["items"].append(
                        {"title": row["title"][:140], "url": row["url"], "when": when}
                    )
            map_svg = render_result_map(result_orgs, caption="Newsrooms in these results",
                                        stories=by_slug)
            if map_svg:
                parts.append(map_svg)
            parts.append(facet_bar(query, tags, region, language, rows))
            parts.append('<div id="feed-items">')
            for row in rows:
                parts.append(render_feed_item(cur, row, with_related=False))
            parts.append("</div>")
            parts.append(pager(query, tags, region, language, page_num, pages))
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
        state = (params.get("state") or [""])[0][:2]
        place = (params.get("place") or [""])[0][:60]
        national = bool(params.get("national"))
        raw_page = (params.get("page") or ["1"])[0]
        page_num = int(raw_page) if raw_page.isdigit() and 1 <= int(raw_page) <= MAX_PAGES else 1
        try:
            if wants_rss:
                body = render_search_rss(query, tags, region, language, subject,
                                         state, place, national).encode("utf-8")
            else:
                body = render(query, tags, region, language, subject, page_num,
                              state, place, national).encode("utf-8")
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
