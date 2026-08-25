"""Generate the static site from the database: plain HTML, no CSS, no JS.

Pages:
    site/index.html         intro, stats, latest headlines
    site/catalog.html       every org, in their own words, grouped by state
    site/map.html           inline-SVG coverage map (Albers projection)
    site/feed.html          combined feed, newest first, with cross-region matches
    site/connections.html   strongest story pairs across regions (pgvector)
    site/orgs/<slug>.html   one page per org
    site/onepage.html       everything on one self-contained page
    data/catalog.json       machine-readable catalog export
"""

import collections
import json
import math
import os
import re
from datetime import datetime, timezone
from html import escape as esc

from . import config
from .albers import MapProjection
from .db import connect

MIN_RELATED_SIM = float(os.environ.get("MIN_RELATED_SIM", "0.30"))
# Above this cosine similarity, or with near-identical headlines, two
# articles are the same story running in multiple outlets (syndication or a
# co-publish), not two newsrooms independently circling one topic.
SAME_STORY_SIM = float(os.environ.get("SAME_STORY_SIM", "0.80"))
FEED_PAGE_ARTICLES = 250
ONEPAGE_ARTICLES = 80

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "Washington, D.C.", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

NAV = [
    ("index.html", "Localpaper"),
    ("catalog.html", "Catalog"),
    ("map.html", "Map"),
    ("feed.html", "Feed"),
    ("connections.html", "Connections"),
]


def page(title, body, prefix="", nav_html=None):
    nav = nav_html or " ·\n".join(f'<a href="{prefix}{href}">{esc(label)}</a>' for href, label in NAV)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
</head>
<body>
<p>{nav}</p>
<hr>
{body}
<hr>
<p><small>Generated {generated}. Catalog descriptions are quoted from each
newsroom's own About page; headlines and summaries come from their public
feeds and link to the original story. Built from
<a href="https://github.com/ftrain/localpaper">ftrain/localpaper</a>.</small></p>
</body>
</html>
"""


def place_label(org):
    if org["city"] and org["state"]:
        return f"{org['city']}, {org['state']}"
    if org["state"]:
        return STATE_NAMES.get(org["state"], org["state"])
    return "no fixed geography"


def org_href(org, mode, prefix=""):
    """Internal org page for the site; the org's own site on the one-pager."""
    if mode == "onepage":
        return org["url"]
    return f"{prefix}orgs/{org['slug']}.html"


def meta_line(org):
    bits = [esc(place_label(org))]
    if org["coverage"]:
        bits.append(f"covers {esc(org['coverage'])}")
    if org["model"]:
        bits.append(esc(org["model"]))
    if org["founded"]:
        bits.append(f"est. {org['founded']}")
    if org["affiliations"]:
        bits.append(esc(", ".join(org["affiliations"])))
    return " · ".join(bits)


def excerpt_paragraphs(text, max_paras=3, max_chars=1100):
    if not text:
        return [], False
    paras = [p for p in text.split("\n\n") if p.strip()]
    out, used = [], 0
    for p in paras[:max_paras]:
        if used + len(p) > max_chars and out:
            break
        out.append(p if used + len(p) <= max_chars else p[: max_chars - used].rsplit(" ", 1)[0] + " […]")
        used += len(p)
    return out, len(out) < len(paras)


def catalog_entry(org, mode, prefix="", full=False):
    lines = [f'<h3 id="{esc(org["slug"])}"><a href="{esc(org["url"])}">{esc(org["name"])}</a></h3>']
    lines.append(f"<p>{meta_line(org)}")
    extras = [f'<a href="{org_href(org, mode, prefix)}">details</a>'] if mode != "onepage" else []
    if org["feed_url"]:
        extras.append(f'<a href="{esc(org["feed_url"])}">RSS</a>')
    if extras:
        lines.append(" · " + " · ".join(extras))
    lines.append("</p>")

    if org["about_text"]:
        if full:
            paras, truncated = [p for p in org["about_text"].split("\n\n") if p.strip()], False
        else:
            paras, truncated = excerpt_paragraphs(org["about_text"])
        inner = "\n".join(f"<p>{esc(p)}</p>" for p in paras)
        lines.append(f"<blockquote>\n{inner}\n</blockquote>")
        src = org["about_source_url"] or org["url"]
        when = org["about_fetched_at"].strftime("%Y-%m-%d") if org["about_fetched_at"] else ""
        more = f' <a href="{esc(src)}">Read the rest on their site.</a>' if truncated else ""
        lines.append(
            f'<p><small>— in their words, from <a href="{esc(src)}">their About page</a> ({when}).{more}</small></p>'
        )
    else:
        lines.append(f'<p><em>About text pending — see <a href="{esc(org["about_url"] or org["url"])}">their site</a>.</em></p>')
    return "\n".join(lines)


def group_orgs_by_state(orgs):
    groups = collections.OrderedDict()
    with_state = sorted(
        (o for o in orgs if o["state"]), key=lambda o: (STATE_NAMES.get(o["state"], o["state"]), o["name"])
    )
    for org in with_state:
        groups.setdefault(STATE_NAMES.get(org["state"], org["state"]), []).append(org)
    national = sorted((o for o in orgs if not o["state"]), key=lambda o: o["name"])
    return groups, national


def render_catalog(orgs, mode="site", prefix=""):
    groups, national = group_orgs_by_state(orgs)
    parts = [
        "<h1>Catalog</h1>",
        f"<p>{len(orgs)} newsrooms, each described in its own words — text quoted "
        "directly from their About pages.</p>",
    ]
    for state_name, group in groups.items():
        parts.append(f"<h2>{esc(state_name)}</h2>")
        parts.extend(catalog_entry(o, mode, prefix) for o in group)
    if national:
        parts.append("<h2>Everywhere (no fixed geography)</h2>")
        parts.extend(catalog_entry(o, mode, prefix) for o in national)
    return "\n".join(parts)


def render_map(orgs, mode="site", prefix=""):
    proj = MapProjection(config.STATES_GEOJSON)
    mappable = [o for o in orgs if o["lat"] and o["lon"] and o["state"] not in (None, "AK", "HI")]
    elsewhere = [o for o in orgs if o not in mappable]

    # Orgs in the same city share coordinates; fan them out in a small ring.
    clusters = collections.defaultdict(list)
    for org in mappable:
        clusters[(round(org["lat"], 1), round(org["lon"], 1))].append(org)

    dots = []
    for cluster in clusters.values():
        cx, cy = proj.to_svg_coords(cluster[0]["lon"], cluster[0]["lat"])
        n = len(cluster)
        for i, org in enumerate(cluster):
            if n == 1:
                x, y = cx, cy
            else:
                angle = 2 * math.pi * i / n
                x, y = cx + 9 * math.cos(angle), cy + 9 * math.sin(angle)
            label = f"{org['name']} — {org['coverage'] or place_label(org)}"
            dots.append(
                f'<a href="{esc(org_href(org, mode, prefix))}">'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="currentColor" fill-opacity="0.75">'
                f"<title>{esc(label)}</title></circle></a>"
            )

    states = "".join(
        f'<path d="{d}" fill="none" stroke="currentColor" stroke-opacity="0.35" stroke-width="1"><title>{esc(name)}</title></path>'
        for name, d in proj.state_paths()
    )
    svg = (
        f'<svg viewBox="0 0 {proj.width} {proj.height}" width="100%" role="img" '
        f'aria-label="Map of the United States with a dot for each newsroom">\n'
        f"{states}\n{''.join(dots)}\n</svg>"
    )

    groups, national = group_orgs_by_state(orgs)
    listing = []
    for state_name, group in groups.items():
        items = ", ".join(
            f'<a href="{esc(org_href(o, mode, prefix))}">{esc(o["name"])}</a>'
            f" ({esc(o['coverage'] or place_label(o))})"
            for o in group
        )
        listing.append(f"<p><strong>{esc(state_name)}</strong>: {items}</p>")
    if national:
        items = ", ".join(f'<a href="{esc(org_href(o, mode, prefix))}">{esc(o["name"])}</a>' for o in national)
        listing.append(f"<p><strong>Everywhere</strong>: {items}</p>")

    off_map = [o for o in elsewhere if o["state"] in ("AK", "HI")]
    off_note = ""
    if off_map:
        names = ", ".join(f'<a href="{esc(org_href(o, mode, prefix))}">{esc(o["name"])}</a> ({place_label(o)})' for o in off_map)
        off_note = f"<p>Beyond the lower 48: {names}.</p>"

    return (
        "<h1>Coverage map</h1>"
        "<p>Every dot is a newsroom; hover for the name, click for the details. "
        "Statewide outlets are plotted at their home city.</p>"
        f"{svg}\n{off_note}\n<h2>By state</h2>\n" + "\n".join(listing)
    )


def day_of(article):
    dt = article["published_at"] or article["fetched_at"]
    return dt.astimezone(timezone.utc).strftime("%A, %B %-d, %Y")


def title_tokens(title):
    from .embedder import _STOPWORDS, _WORD_RE

    # Normalize typographic apostrophes and strip possessives, so one
    # outlet's "West's" matches another's "West’s".
    norm = title.lower().replace("’", "'").replace("‘", "'")
    words = (re.sub(r"'s$", "", w).replace("'", "") for w in _WORD_RE.findall(norm))
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def classify_pair(sim, title_a, title_b):
    """'same' = one story in two outlets; 'kindred' = distinct stories that
    rhyme; None = too weak to show. Embedding similarity alone can't split
    reprints from echoes (a retitled reprint scores ~0.89 but co-published
    copies with differently-truncated summaries score ~0.6, while
    independent coverage of one event scores ~0.35), so headlines carry
    half the decision."""
    a, b = title_tokens(title_a), title_tokens(title_b)
    union = a | b
    jac = len(a & b) / len(union) if union else 0.0
    if sim >= SAME_STORY_SIM:
        return "same"
    if jac >= 0.75 and min(len(a), len(b)) >= 4:
        return "same"
    if sim >= 0.50 and jac >= 0.40:
        return "same"
    if sim >= MIN_RELATED_SIM and (len(a & b) >= 1 or sim >= 0.45):
        # The shared-headline-token guard keeps out spurious hashing
        # collisions between short or unrelated titles.
        return "kindred"
    return None


def related_to(cur, article_id, limit=4):
    cur.execute(
        """
        SELECT b.title, b.url, o2.name, o2.slug, o2.url AS org_url,
               1 - (a.embedding <=> b.embedding) AS sim
        FROM articles a
        JOIN orgs o1 ON o1.id = a.org_id,
        articles b JOIN orgs o2 ON o2.id = b.org_id
        WHERE a.id = %s AND b.org_id <> a.org_id
          AND o2.state IS DISTINCT FROM o1.state
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
        ORDER BY a.embedding <=> b.embedding
        LIMIT %s
        """,
        (article_id, limit),
    )
    return [r for r in cur.fetchall() if r[5] >= 0.28]


def render_feed(cur, articles, mode="site", prefix="", with_related=True):
    parts = [
        "<h1>The feed</h1>",
        "<p>Newest stories from every newsroom in the catalog, together. "
        "Indented lines come from the vector index: <em>same story also "
        "running in</em> marks a reprint or co-publish in another outlet, and "
        "<em>echo</em> marks a distinct story from another region that rhymes "
        "with the one above.</p>",
    ]
    current_day = None
    open_list = False
    for a in articles:
        day = day_of(a)
        if day != current_day:
            if open_list:
                parts.append("</ul>")
            parts.append(f"<h2>{esc(day)}</h2>")
            parts.append("<ul>")
            current_day, open_list = day, True
        org_page = a["org_url"] if mode == "onepage" else f"{prefix}orgs/{a['slug']}.html"
        org_link = f'<a href="{esc(org_page)}">{esc(a["org_name"])}</a>'
        item = f"<li>{org_link}: <a href=\"{esc(a['url'])}\">{esc(a['title'])}</a>"
        if with_related:
            same_copies, echoes = [], []
            for r_title, r_url, r_org, r_slug, r_org_url, r_sim in related_to(cur, a["id"]):
                cls = classify_pair(r_sim, a["title"], r_title)
                org_page = r_org_url if mode == "onepage" else f"{prefix}orgs/{r_slug}.html"
                if cls == "same":
                    same_copies.append(f'<a href="{esc(r_url)}">{esc(r_org)}</a>')
                elif cls == "kindred" and len(echoes) < 2:
                    echoes.append(
                        f'<li>echo in <a href="{esc(org_page)}">{esc(r_org)}</a>: '
                        f'<a href="{esc(r_url)}">{esc(r_title)}</a> <small>(sim {r_sim:.2f})</small></li>'
                    )
            sub = ""
            if same_copies:
                sub += f'<li>same story also running in {" · ".join(same_copies)}</li>'
            sub += "".join(echoes)
            if sub:
                item += f"<ul>{sub}</ul>"
        item += "</li>"
        parts.append(item)
    if open_list:
        parts.append("</ul>")
    return "\n".join(parts)


def gather_connections(cur):
    """Cross-state neighbor pairs, split into same-story clusters and
    kindred (distinct-story) pairs."""
    cur.execute(
        """
        WITH recent AS (
            SELECT a.id, a.title, a.url, a.embedding, a.published_at,
                   o.name AS org_name, o.slug, o.url AS org_url, o.state
            FROM articles a JOIN orgs o ON o.id = a.org_id
            WHERE a.embedding IS NOT NULL
              AND coalesce(a.published_at, a.fetched_at) > now() - interval '60 days'
        )
        SELECT a.id, a.title, a.url, a.org_name, a.slug, a.org_url, a.state, a.published_at,
               m.id, m.title, m.url, m.org_name, m.slug, m.org_url, m.state, m.published_at, m.sim
        FROM recent a
        JOIN LATERAL (
            SELECT b.*, 1 - (a.embedding <=> b.embedding) AS sim
            FROM recent b
            WHERE b.state IS DISTINCT FROM a.state
            ORDER BY a.embedding <=> b.embedding
            LIMIT 3
        ) m ON true
        WHERE m.sim >= 0.28
        """
    )
    cols = ("id", "title", "url", "org_name", "slug", "org_url", "state", "published_at")
    articles, pairs = {}, {}
    for row in cur.fetchall():
        a = dict(zip(cols, row[:8]))
        b = dict(zip(cols, row[8:16]))
        sim = row[16]
        articles[a["id"]] = a
        articles.setdefault(b["id"], b)
        key = (min(a["id"], b["id"]), max(a["id"], b["id"]))
        if key not in pairs or sim > pairs[key][0]:
            pairs[key] = (sim, classify_pair(sim, a["title"], b["title"]))

    # Union-find over same-story pairs -> reprint/co-publish clusters.
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (ia, ib), (sim, cls) in pairs.items():
        if cls == "same":
            parent[find(ia)] = find(ib)

    clusters = collections.defaultdict(list)
    for aid in list(parent):
        clusters[find(aid)].append(articles[aid])
    clusters = [
        sorted(members, key=lambda m: (m["published_at"] is None, m["published_at"], m["id"]))
        for members in clusters.values()
        if len(members) > 1
    ]
    clusters.sort(key=len, reverse=True)

    # Kindred pairs, deduped so one logical story-pair doesn't appear once
    # per reprint copy: collapse each article to its cluster root first.
    best = {}
    for (ia, ib), (sim, cls) in pairs.items():
        if cls != "kindred":
            continue
        ra, rb = find(ia), find(ib)
        if ra == rb:
            continue
        key = (min(ra, rb), max(ra, rb))
        if key not in best or sim > best[key][0]:
            best[key] = (sim, ia, ib)
    kindred = sorted(best.values(), reverse=True)
    return clusters, kindred, articles


def _org_line(art, mode, prefix):
    loc = STATE_NAMES.get(art["state"], art["state"]) if art["state"] else "everywhere"
    href = art["org_url"] if mode == "onepage" else f"{prefix}orgs/{art['slug']}.html"
    return f'<a href="{esc(href)}">{esc(art["org_name"])}</a> ({esc(loc)})'


def render_connections(cur, mode="site", prefix="", limit=20):
    clusters, kindred, articles = gather_connections(cur)

    parts = [
        "<h1>Connections across regions</h1>",
        "<p>Article embeddings live in Postgres with pgvector, so we can ask "
        "which stories from <em>different states</em> sit closest together. "
        "The answers come in two kinds, and they're separated here.</p>",
        "<h2>One story, many mastheads</h2>",
        "<p>Near-identical matches are the same story running in several "
        "outlets — collaborations, shared statehouse desks, and networks like "
        "Deep South Today publishing across their newsrooms. That sharing is "
        "part of how this model survives; here's the network showing itself.</p>",
    ]
    if clusters:
        parts.append("<ul>")
        for members in clusters[:limit]:
            rep = members[0]
            outlets = " · ".join(
                f'<a href="{esc(m["url"])}">{esc(m["org_name"])}</a>' for m in members
            )
            parts.append(
                f'<li><p><a href="{esc(rep["url"])}">{esc(rep["title"])}</a><br>'
                f"<small>running in {len(members)} outlets: {outlets}</small></p></li>"
            )
        parts.append("</ul>")
    else:
        parts.append("<p><em>No shared stories detected in this crawl.</em></p>")

    parts += [
        "<h2>Kindred stories, different places</h2>",
        "<p>These are <em>distinct</em> stories — separate newsrooms, separate "
        "reporting — that the vector index says rhyme. The same pressures land "
        "on every town: housing, schools, water, fire, policing, money.</p>",
    ]
    if kindred:
        parts.append("<ol>")
        for sim, ia, ib in kindred[:limit]:
            a, b = articles[ia], articles[ib]
            parts.append(
                "<li><p>"
                f'<a href="{esc(a["url"])}">{esc(a["title"])}</a><br><small>{_org_line(a, mode, prefix)}</small><br>'
                f'↔ <a href="{esc(b["url"])}">{esc(b["title"])}</a><br><small>{_org_line(b, mode, prefix)} '
                f"· similarity {sim:.2f}</small></p></li>"
            )
        parts.append("</ol>")
    else:
        parts.append("<p><em>No strong cross-region pairs yet — run the feed crawler a few more times.</em></p>")
    return "\n".join(parts)


def render_index(cur, orgs, articles, mode="site"):
    one = mode == "onepage"
    n_states = len({o["state"] for o in orgs if o["state"]})
    cur.execute("SELECT count(*) FROM articles")
    n_articles = cur.fetchone()[0]
    coops = sum(1 for o in orgs if "worker" in (o["model"] or ""))
    parts = [
        "<h1>Localpaper</h1>",
        "<p><strong>A steady feed of the local news being built to last.</strong></p>",
        "<p>American local news wasn't killed by the internet alone; a lot of it "
        "was stripped for parts. But all over the country, journalists and "
        "communities are building something better in its place: nonprofit "
        "newsrooms, worker-owned cooperatives, century-old family papers trying "
        "new models, metro dailies handed to civic institutions instead of "
        "hedge funds. They answer to readers and neighbors, not shareholders. "
        "They are surviving — many are growing.</p>",
        "<p>This site is a catalog of those newsrooms in their own words, a map "
        "of who covers where, and one combined feed of what they published "
        "today. Everything links back to them; go read them, subscribe, become "
        "a member.</p>",
        f"<p><strong>{len(orgs)}</strong> newsrooms · <strong>{n_states}</strong> states and D.C. · "
        f"<strong>{coops}</strong> worker-owned or worker-led · <strong>{n_articles}</strong> stories in the feed.</p>",
        f'<ul><li><a href="{"#catalog" if one else "catalog.html"}">The catalog</a> — every newsroom, described in its own words</li>'
        f'<li><a href="{"#map" if one else "map.html"}">The map</a> — who covers where</li>'
        f'<li><a href="{"#feed" if one else "feed.html"}">The feed</a> — what they published, newest first</li>'
        f'<li><a href="{"#connections" if one else "connections.html"}">Connections</a> — kindred stories across regions, via vector search</li></ul>',
        "<p>Guides to this movement, and where many of these newsrooms found "
        'backing: the <a href="https://www.lenfestinstitute.org/">Lenfest Institute</a> '
        '(including its <a href="https://www.lenfestinstitute.org/institute-news/beyond-print-launches-11-newspaper-business-transformation-experiments/">Beyond Print</a> cohort), '
        'the <a href="https://www.theajp.org/">American Journalism Project</a>, '
        'the <a href="https://inn.org/">Institute for Nonprofit News</a>, and '
        '<a href="https://rjionline.org/news/what-is-a-non-traditional-newsroom/">RJI on non-traditional newsrooms</a>.</p>',
        "<h2>Latest from the feed</h2>",
        "<ul>",
    ]
    # Keep the front page varied: at most two consecutive-list items per org.
    latest, per_org = [], collections.Counter()
    for a in articles:
        if per_org[a["slug"]] < 2:
            latest.append(a)
            per_org[a["slug"]] += 1
        if len(latest) == 15:
            break
    for a in latest:
        org_page = a["org_url"] if one else f'orgs/{a["slug"]}.html'
        parts.append(
            f'<li><a href="{esc(org_page)}">{esc(a["org_name"])}</a>: '
            f'<a href="{esc(a["url"])}">{esc(a["title"])}</a></li>'
        )
    parts.append("</ul>")
    parts.append(f'<p><a href="{"#feed" if one else "feed.html"}">The whole feed →</a></p>')
    return "\n".join(parts)


def render_org_page(cur, org):
    parts = [f'<h1><a href="{esc(org["url"])}">{esc(org["name"])}</a></h1>', f"<p>{meta_line(org)}</p>"]
    if org["feed_url"]:
        parts.append(f'<p><a href="{esc(org["feed_url"])}">RSS feed</a></p>')
    if org["about_text"]:
        paras = [p for p in org["about_text"].split("\n\n") if p.strip()]
        inner = "\n".join(f"<p>{esc(p)}</p>" for p in paras)
        src = org["about_source_url"] or org["url"]
        when = org["about_fetched_at"].strftime("%Y-%m-%d") if org["about_fetched_at"] else ""
        parts.append("<h2>In their words</h2>")
        parts.append(f"<blockquote>\n{inner}\n</blockquote>")
        parts.append(f'<p><small>— from <a href="{esc(src)}">their About page</a>, fetched {when}.</small></p>')
    cur.execute(
        """SELECT title, url, published_at, fetched_at FROM articles
           WHERE org_id = %s ORDER BY coalesce(published_at, fetched_at) DESC LIMIT 20""",
        (org["id"],),
    )
    stories = cur.fetchall()
    if stories:
        parts.append("<h2>Recent stories</h2><ul>")
        for title, url, published, fetched in stories:
            when = (published or fetched).astimezone(timezone.utc).strftime("%b %-d")
            parts.append(f'<li>{esc(when)} — <a href="{esc(url)}">{esc(title)}</a></li>')
        parts.append("</ul>")
    return "\n".join(parts)


ORG_COLUMNS = (
    "id", "slug", "name", "url", "about_url", "feed_url", "city", "state", "lat", "lon",
    "coverage", "coverage_type", "model", "affiliations", "founded",
    "about_text", "about_source_url", "about_fetched_at",
)


def load_orgs(cur):
    cur.execute(f"SELECT {', '.join(ORG_COLUMNS)} FROM orgs ORDER BY name")
    return [dict(zip(ORG_COLUMNS, row)) for row in cur.fetchall()]


def load_articles(cur, limit):
    cur.execute(
        """
        SELECT a.id, a.url, a.title, a.published_at, a.fetched_at,
               o.name AS org_name, o.slug, o.url AS org_url
        FROM articles a JOIN orgs o ON o.id = a.org_id
        ORDER BY coalesce(a.published_at, a.fetched_at) DESC
        LIMIT %s
        """,
        (limit,),
    )
    cols = ("id", "url", "title", "published_at", "fetched_at", "org_name", "slug", "org_url")
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def export_catalog_json(orgs):
    out = []
    for o in orgs:
        rec = {k: o[k] for k in ORG_COLUMNS if k not in ("id",)}
        if rec["about_fetched_at"]:
            rec["about_fetched_at"] = rec["about_fetched_at"].isoformat()
        out.append(rec)
    path = config.DATA_DIR / "catalog.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return path


def main():
    site = config.SITE_DIR
    (site / "orgs").mkdir(parents=True, exist_ok=True)

    with connect() as conn, conn.cursor() as cur:
        orgs = load_orgs(cur)
        articles = load_articles(cur, FEED_PAGE_ARTICLES)

        (site / "index.html").write_text(page("Localpaper", render_index(cur, orgs, articles)))
        (site / "catalog.html").write_text(page("Localpaper — Catalog", render_catalog(orgs)))
        (site / "map.html").write_text(page("Localpaper — Coverage map", render_map(orgs)))
        (site / "feed.html").write_text(page("Localpaper — The feed", render_feed(cur, articles)))
        (site / "connections.html").write_text(page("Localpaper — Connections", render_connections(cur)))

        for org in orgs:
            body = render_org_page(cur, org)
            (site / "orgs" / f"{org['slug']}.html").write_text(
                page(f"Localpaper — {org['name']}", body, prefix="../")
            )

        onepage_articles = articles[:ONEPAGE_ARTICLES]
        onepage = "\n<hr>\n".join(
            [
                render_index(cur, orgs, onepage_articles, mode="onepage"),
                '<div id="catalog">' + render_catalog(orgs, mode="onepage") + "</div>",
                '<div id="map">' + render_map(orgs, mode="onepage") + "</div>",
                '<div id="feed">' + render_feed(cur, onepage_articles, mode="onepage") + "</div>",
                '<div id="connections">' + render_connections(cur, mode="onepage") + "</div>",
            ]
        )
        onepage_nav = (
            '<p><strong>Localpaper</strong> · <a href="#catalog">Catalog</a> · <a href="#map">Map</a> · '
            '<a href="#feed">Feed</a> · <a href="#connections">Connections</a></p>'
        )
        (site / "onepage.html").write_text(page("Localpaper", onepage, nav_html=onepage_nav))

        path = export_catalog_json(orgs)
        print(f"built site/ ({len(orgs)} orgs, {len(articles)} feed items) and {path.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
