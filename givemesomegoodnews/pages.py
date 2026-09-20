"""The pages that are not the feed.

The catalog and its state and tag pages, the coverage map, a page per
newsroom, the resources behind them, the RSS index, the two connections
pages, and the plain-text edition. Each is a function that returns a body;
the shell goes around it and build_site decides where it is written.
"""

import collections
import json
import math
import os
import re
from datetime import timezone
from html import escape as esc

from . import config, mapbox
from .albers import MapProjection
from .cards import (meta_line, ownership_tags, reporter_facts, reporter_of,
                    tag_links)
from .dedupe import classify_pair
from .links import (STATE_NAMES, _org_line, org_href, place_label, state_href)
from .prose import about_opening, excerpt_paragraphs, usable_about
from .tags import tag_slug
from .timezones import local_dateline


ONEPAGE_ARTICLES = 80


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
        f"<p>{len(orgs)} newsrooms. Text quoted from their own About pages.</p>",
    ]
    for state_name, group in groups.items():
        parts.append(f"<h2>{esc(state_name)}</h2>")
        parts.extend(catalog_entry(o, mode, prefix) for o in group)
    if national:
        parts.append("<h2>Everywhere (no fixed geography)</h2>")
        parts.extend(catalog_entry(o, mode, prefix) for o in national)
    return "\n".join(parts)


def render_result_map(orgs, prefix="", caption="Where these newsrooms are", stories=None,
                      box=mapbox.WHOLE):
    """A compact map of one subset of newsrooms — used above search results.

    `box` is the area the search is limited to, drawn over the map as a
    shaded rectangle the reader can take hold of; the whole map is the
    default and filters nothing. It is drawn here, server-side, rather than
    by the script that makes it draggable, so a search narrowed to one
    corner of the country still shows which corner with scripting off, and
    so the box is on screen in the same paint as the dots underneath it.
    """
    proj = MapProjection(config.STATES_GEOJSON)
    mappable = [o for o in orgs if o.get("lat") and o.get("lon") and o.get("state")]
    # With a box drawn there is always a map: it is the only way back to the
    # whole country, and a narrowing that leaves nothing has to show that it
    # was the narrowing that did it.
    if not mappable and box == mapbox.WHOLE:
        return ""
    clusters = collections.defaultdict(list)
    for org in mappable:
        clusters[(org["state"], round(org["lat"], 1), round(org["lon"], 1))].append(org)
    dots = []
    for (state, _la, _lo), cluster in clusters.items():
        cx, cy = proj.to_svg_coords(cluster[0]["lon"], cluster[0]["lat"], state)
        for i, org in enumerate(cluster):
            angle = 2 * math.pi * i / max(len(cluster), 1)
            x = cx + (0 if len(cluster) == 1 else 9 * math.cos(angle))
            y = cy + (0 if len(cluster) == 1 else 9 * math.sin(angle))
            # Without JavaScript the dot is still a link to the newsroom;
            # with it, the click opens a preview over the map instead.
            dots.append(
                f'<a href="{prefix}orgs/{esc(org["slug"])}.html" '
                f'data-slug="{esc(org["slug"])}">'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="#c8102e" fill-opacity="0.9">'
                f'<title>{esc(org["name"])}</title></circle></a>'
            )
    states = "".join(
        f'<path d="{d}" fill="none" stroke="currentColor" stroke-opacity="0.3" stroke-width="1"/>'
        for _name, d in proj.state_paths()
    )
    names = ", ".join(sorted({o["name"] for o in mappable}))
    payload = ""
    if stories:
        payload = ('<script type="application/json" id="map-stories">'
                   + json.dumps(stories) + "</script>")
    x0, y0, x1, y1 = box
    area = (f'<div class="mapbox" data-box="{mapbox.unparse(box)}" '
            f'style="left:{x0 / 10:g}%;top:{y0 / 10:g}%;'
            f'width:{(x1 - x0) / 10:g}%;height:{(y1 - y0) / 10:g}%"></div>')
    return (
        f'<figure class="mapwrap">'
        f'<div class="mapframe">'
        f'<svg viewBox="0 0 {proj.width} {proj.height}" width="100%" role="img" '
        f'aria-label="{esc(caption)}: {esc(names[:600])}">{states}{"".join(dots)}</svg>'
        f'{area}</div>'
        f'<div class="preview" hidden><button type="button" class="preview-close" '
        f'aria-label="Close">&times;</button><div class="preview-body"></div></div>'
        f'<figcaption class="meta">{esc(caption)} &mdash; {len(mappable)} newsrooms. '
        f'Tap a dot for its stories.<span class="boxhint"></span></figcaption>'
        f"{payload}</figure>"
    )


def render_map(orgs, mode="site", prefix="", recent=()):
    proj = MapProjection(config.STATES_GEOJSON)
    mappable = [o for o in orgs if o["lat"] and o["lon"] and o["state"]]
    placed = {o["slug"] for o in mappable}

    # Orgs in the same city share coordinates; fan them out in a small ring.
    clusters = collections.defaultdict(list)
    for org in mappable:
        clusters[(org["state"], round(org["lat"], 1), round(org["lon"], 1))].append(org)

    dots = []
    for (state, _lat, _lon), cluster in clusters.items():
        cx, cy = proj.to_svg_coords(cluster[0]["lon"], cluster[0]["lat"], state)
        n = len(cluster)
        for i, org in enumerate(cluster):
            if n == 1:
                x, y = cx, cy
            else:
                angle = 2 * math.pi * i / n
                x, y = cx + 9 * math.cos(angle), cy + 9 * math.sin(angle)
            fresh = org["slug"] in recent
            fill = "#c8102e" if fresh else "currentColor"
            opacity = "0.95" if fresh else "0.55"
            note = " — published today" if fresh else ""
            label = f"{org['name']} — {org['coverage'] or place_label(org)}{note}"
            dots.append(
                f'<a href="{esc(org_href(org, mode, prefix))}">'
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{5.5 if fresh else 4.5}" '
                f'fill="{fill}" fill-opacity="{opacity}">'
                f"<title>{esc(label)}</title></circle></a>"
            )

    states = "".join(
        f'<path d="{d}" fill="none" stroke="currentColor" stroke-opacity="0.35" '
        f'stroke-width="1"><title>{esc(name)}</title></path>'
        for name, d in proj.state_paths()
    )
    # Territories with no outline in the geojson get a labelled marker.
    marks = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="currentColor" fill-opacity="0.3"></circle>'
        f'<text x="{x + 6:.1f}" y="{y + 4:.1f}" font-size="11" fill="currentColor" '
        f'fill-opacity="0.55">{esc(label)}</text>'
        for _code, label, x, y in proj.territory_labels()
    )
    svg = (
        f'<svg viewBox="0 0 {proj.width} {proj.height}" width="100%" role="img" '
        f'aria-label="Map of the United States, its territories, and a dot for each newsroom">\n'
        f"{states}\n{marks}\n{''.join(dots)}\n</svg>"
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
        items = ", ".join(
            f'<a href="{esc(org_href(o, mode, prefix))}">{esc(o["name"])}</a>' for o in national
        )
        listing.append(f"<p><strong>Everywhere</strong>: {items}</p>")

    unplaced = [o for o in orgs if o["slug"] not in placed and o["state"]]
    note = ""
    if unplaced:
        names = ", ".join(
            f'<a href="{esc(org_href(o, mode, prefix))}">{esc(o["name"])}</a>' for o in unplaced
        )
        note = f"<p><small>No coordinates yet: {names}.</small></p>"

    return (
        "<h1>Coverage map</h1>"
        "<p>One dot per newsroom; statewide outlets plotted at their home city. "
        '<span style="color:#c8102e">Red</span> means they published in the last 24 hours. '
        "Alaska, Hawaii and Puerto Rico are drawn as insets.</p>"
        f"{svg}\n{note}\n<h2>By state</h2>\n" + "\n".join(listing)
    )


def gather_connections(cur):
    """Cross-state neighbor pairs, split into same-story clusters and
    kindred (distinct-story) pairs."""
    # Anchor on a bounded set of the newest stories and let the HNSW index
    # find each one's neighbours in the articles table. The earlier version
    # put both sides in a CTE, which no index can serve: at a thousand feeds
    # that became roughly 225 million distance computations per build.
    cur.execute(
        """
        WITH anchors AS (
            SELECT a.id, a.title, a.url, a.embedding, a.published_at,
                   o.name AS org_name, o.slug, o.url AS org_url, o.state
            FROM articles a JOIN orgs o ON o.id = a.org_id
            WHERE a.embedding IS NOT NULL
              AND coalesce(a.published_at, a.fetched_at) > now() - interval '14 days'
            ORDER BY coalesce(a.published_at, a.fetched_at) DESC
            LIMIT %s
        )
        SELECT a.id, a.title, a.url, a.org_name, a.slug, a.org_url, a.state, a.published_at,
               m.id, m.title, m.url, m.org_name, m.slug, m.org_url, m.state, m.published_at, m.sim
        FROM anchors a
        JOIN LATERAL (
            SELECT b.id, b.title, b.url, o2.name AS org_name, o2.slug,
                   o2.url AS org_url, o2.state, b.published_at,
                   1 - (a.embedding <=> b.embedding) AS sim
            FROM articles b JOIN orgs o2 ON o2.id = b.org_id
            WHERE b.embedding IS NOT NULL
              AND b.id <> a.id
              AND o2.state IS DISTINCT FROM a.state
            ORDER BY a.embedding <=> b.embedding
            LIMIT 3
        ) m ON true
        WHERE m.sim >= 0.28
        """,
        (CONNECTION_ANCHORS,),
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


def render_big_stories(cur, mode="site", prefix="", limit=40):
    """One story carried by many newsrooms — the day's biggest, measured by
    how many newsrooms independently ran it."""
    clusters, _kindred, _articles = gather_connections(cur)
    parts = ["<h1>Big stories</h1>",
             "<p>Stories running in several newsrooms at once, most-carried first.</p>"]
    if not clusters:
        parts.append("<p>Nothing shared across newsrooms in this crawl yet.</p>")
        return "\n".join(parts)
    parts.append("<ul>")
    for members in sorted(clusters, key=len, reverse=True)[:limit]:
        rep = members[0]
        outlets = " &middot; ".join(
            f'<a href="{esc(m["url"])}">{esc(m["org_name"])}</a>' for m in members
        )
        parts.append(
            f'<li><a href="{esc(rep["url"])}">{esc(rep["title"])}</a>'
            f'<br><span class="meta">in {len(members)} newsrooms: {outlets}</span></li>'
        )
    parts.append("</ul>")
    return "\n".join(parts)


def render_story_links(cur, mode="site", prefix="", limit=40):
    """Separate reporting, in different places, on the same pressure."""
    _clusters, kindred, articles = gather_connections(cur)
    parts = ["<h1>Story links</h1>",
             "<p>Separate newsrooms, separate reporting, the same pressure landing "
             "in two places. Paired by the vector index.</p>"]
    if not kindred:
        parts.append("<p>No strong cross-region pairs yet.</p>")
        return "\n".join(parts)
    parts.append("<ul>")
    for sim, ia, ib in kindred[:limit]:
        a, b = articles[ia], articles[ib]
        parts.append(
            f'<li><a href="{esc(a["url"])}">{esc(a["title"])}</a>'
            f'<br><span class="meta">{_org_line(a, mode, prefix)}</span>'
            f'<br><a href="{esc(b["url"])}">{esc(b["title"])}</a>'
            f'<br><span class="meta">{_org_line(b, mode, prefix)}</span></li>'
        )
    parts.append("</ul>")
    return "\n".join(parts)


def render_about(cur, orgs):
    """What this is, where the data came from, and who deserves the credit."""
    cur.execute("SELECT count(*) FROM articles")
    n_articles = cur.fetchone()[0]
    n_states = len({o["state"] for o in orgs if o["state"]})
    cur.execute("SELECT count(*) FROM orgs WHERE support_url IS NOT NULL")
    n_support = cur.fetchone()[0]
    return f"""<h1>About this site</h1>
<p>A reading list of local newsrooms that are built to last, and a feed of
what they published. {len(orgs)} newsrooms across {n_states} states and
territories; {n_articles} stories; {n_support} of them with a link that lets
you pay them directly.</p>

<h2>What is here and what is not</h2>
<p>Nonprofits, co-ops, family and community papers, Native-owned outlets,
the Black-owned and Spanish-language press, college newsrooms, and small
literary and arts magazines. No chains, no hedge-fund papers, no metro
dailies. Obituaries, death notices and horoscopes are kept off the front
page; they are still in the archive and still searchable.</p>

<h2>How it works</h2>
<p>Each newsroom's own RSS feed is read every three hours. Headlines,
summaries and pictures come from those feeds and link back to the original.
Subjects are assigned from the publisher's own categories, then from URL
paths, then by nearest neighbour over article embeddings — no language model
is involved anywhere. Pictures are downloaded and served from here rather
than hotlinked, so a publisher's server is hit once per image instead of
once per reader.</p>

<h2>Credit</h2>
<p>Every story belongs to the newsroom that reported it. Nothing here is
this site's journalism, and nothing in the feeds claims otherwise.</p>
<p>The newsroom directory was compiled by the
<a href="https://www.mediaanddemocracyproject.org/journalism-directory">Media
and Democracy Project</a>. Coordinates come from the U.S. Census Bureau
gazetteer. Catalog descriptions are quoted from each newsroom's own About
page. The funders, networks and associations that keep this sector alive are
listed under <a href="resources.html">Resources</a>.</p>

<h2>Reading it elsewhere</h2>
<p><a href="feeds.html">RSS feeds</a> for everything, for each subject and
for each tag; a <a href="/text/">plain text edition</a> with no
images or scripts; and any <a href="/search">search</a> can be subscribed to
as a feed.</p>

<h2>Robots</h2>
<p>Automated bulk collection of these stories is refused. They are not this
site's to give away.</p>"""


def render_feeds_page(subject_counts, tags):
    """Every feed on offer, in one place rather than buried in a menu."""
    parts = [
        "<h1>RSS feeds</h1>",
        "<p>Every feed carries the full item: headline, summary, byline, the "
        "newsroom that reported it, and the link that lets you pay them. "
        "Nothing here asks you to come back to this site.</p>",
        '<h2>Everything</h2><ul><li><a href="feed.xml">All newsrooms</a></li></ul>',
    ]
    if subject_counts:
        parts.append("<h2>By subject</h2><ul>")
        for name, count in subject_counts:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            parts.append(f'<li><a href="subjects/{slug}.xml">{esc(name)}</a> '
                         f'<span class="meta">{count} stories</span></li>')
        parts.append("</ul>")
    if tags:
        parts.append("<h2>By tag</h2><ul>")
        for tag in tags:
            parts.append(f'<li><a href="tags/{tag_slug(tag)}.xml">{esc(tag)}</a></li>')
        parts.append("</ul>")
    return "\n".join(parts)


def render_catalog_index(orgs, prefix=""):
    """Counts, feature facets, and a way into each state."""
    groups, national = group_orgs_by_state(orgs)
    parts = [
        "<h1>Catalog</h1>",
        f"<p>{len(orgs)} newsrooms.</p>",
    ]
    parts.append("<h2>By state</h2><p>" + " · ".join(
        f'<a href="{state_href(name, prefix)}">{esc(name)}</a> ({len(group)})'
        for name, group in groups.items()
    ) + "</p>")
    if national:
        parts.append("<h2>Everywhere</h2>")
        parts.append(render_org_list(national, prefix="", mode="site"))
    return "\n".join(parts)


def render_org_list(group, prefix="", mode="site"):
    rows = []
    for org in group:
        line = f'<a href="{esc(org_href(org, mode, prefix))}">{esc(org["name"])}</a>'
        tags = tag_links(org, prefix)
        where = esc(org["coverage"] or place_label(org))
        rows.append(f"<li>{line} — {where}"
                    + (f"<br><small>{tags}</small>" if tags else "")
                    + "</li>")
    return "<ul>" + "".join(rows) + "</ul>"


KIND_LABELS = {
    "funder": "Funders",
    "program": "Programs",
    "association": "Associations",
    "network": "Newsroom networks",
    "research": "Research and directories",
}


def load_institutions(cur):
    cur.execute(
        "SELECT slug, name, url, kind, affiliation, about_text, about_source_url, tagline "
        "FROM institutions ORDER BY name"
    )
    cols = ("slug", "name", "url", "kind", "affiliation", "about_text",
            "about_source_url", "tagline")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def render_institutions(cur, orgs, mode="site", prefix=""):
    """Who pays for, convenes, and counts these newsrooms."""
    insts = load_institutions(cur)
    by_affiliation = collections.defaultdict(list)
    for org in orgs:
        for name in (org.get("affiliations") or []):
            by_affiliation[name].append(org)

    parts = [
        "<h1>Resources</h1>",
        f"<p>{len(insts)} organisations that fund, convene, or count the newsrooms "
        "in this catalog. Described in their own words, as the newsrooms are.</p>",
    ]
    grouped = collections.defaultdict(list)
    for inst in insts:
        grouped[inst["kind"] or "research"].append(inst)

    for kind, label in KIND_LABELS.items():
        group = grouped.get(kind)
        if not group:
            continue
        parts.append(f"<h2>{esc(label)}</h2>")
        for inst in group:
            parts.append("<article>")
            parts.append(
                f'<h2><a href="{esc(inst["url"])}">{esc(inst["name"])}</a></h2>'
            )
            line = inst["tagline"] or ""
            if line:
                parts.append(f"<p>{esc(line)}</p>")
            elif inst["about_text"]:
                parts.append(f"<p>{esc(inst['about_text'].split(chr(10))[0][:240])}</p>")
            else:
                parts.append(
                    "<p><small>Their site blocks automated readers, so there is no "
                    "quotation here — the link goes to them.</small></p>"
                )
            if inst["about_source_url"]:
                parts.append(
                    f'<p><small>— from <a href="{esc(inst["about_source_url"])}">'
                    "their About page</a></small></p>"
                )
            members = by_affiliation.get(inst["affiliation"] or "", [])
            if members:
                links = ", ".join(
                    f'<a href="{esc(org_href(o, mode, prefix))}">{esc(o["name"])}</a>'
                    for o in sorted(members, key=lambda o: o["name"])
                )
                noun = "newsroom" if len(members) == 1 else "newsrooms"
                parts.append(f"<p><small>{len(members)} {noun} here: {links}</small></p>")
            parts.append("</article>")
    return "\n".join(parts)


TEXT_CSS = """<style>
body{font-family:Plex,system-ui,sans-serif;font-size:1.25rem;line-height:1.7;
max-width:34rem;margin:0 auto;padding:1rem 1rem 4rem;color:#111;background:#fff}
@media(prefers-color-scheme:dark){body{color:#eee;background:#111}a{color:#ff8080}}
a{color:#b3000f}
h1{font-size:1.6rem}h2{font-size:1.25rem;margin:2rem 0 .3rem}
dl{margin:.2rem 0 .6rem}dt{font-weight:700}dd{margin:0 0 .3rem}
:focus-visible{outline:3px solid currentColor;outline-offset:2px}
.skip{position:absolute;left:-9999px}.skip:focus{position:static;display:block}
article{margin:0 0 2rem;padding-bottom:1rem;border-bottom:1px solid currentColor}
/* The one disclosure in this edition. It keeps its display:list-item, which
   is what its expanded/collapsed announcement depends on, and carries its
   own cue rather than the browser's triangle. */
summary{cursor:pointer;font-size:.95rem;list-style:none;width:max-content;
max-width:100%;border:1px solid currentColor;border-radius:1rem;padding:.05rem .7rem}
summary::-webkit-details-marker{display:none}
/* Drawn, not written: a glyph here would be read out as part of the button.
   This edition is the one a screen reader is most likely to be on. */
summary::after{content:"";display:inline-block;width:0;height:0;margin-left:.5em;
border:.3em solid transparent;border-top-color:currentColor;vertical-align:-.05em}
details[open] summary::after{border-top-color:transparent;
border-bottom-color:currentColor;vertical-align:.25em}
details[open] dl{margin-top:.5rem}
</style>"""


def text_page(title, body, prefix=""):
    """The plain edition: no images, no scripts, one column, real landmarks.

    Everything a sighted reader gets from layout is written out here instead
    — who published it, where they are, when, and how to support them — in
    the order a screen reader will read it.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">
{TEXT_CSS}
</head>
<body>
<a class="skip" href="#main">Skip to the stories</a>
<header>
<nav aria-label="Sections">
<p><a href="index.html">Stories</a> &middot;
<a href="catalog.html">Newsrooms</a> &middot;
<a href="/search">Search</a> &middot;
<a href="/">Full version</a></p>
</nav>
</header>
<main id="main">
{body}
</main>
<footer>
<p>Every story belongs to the newsroom that reported it. Please read it there,
and pay them if you can.</p>
</footer>
</body>
</html>
"""


def render_text_item(a, prefix="../"):
    """Source and headline, and nothing else until it is asked for.

    A screen reader going down a feed wants the newsroom and the headline —
    not a byline, a timestamp, a subject and a list of tags before every
    single item. The rest goes in a <details>, which reads as a collapsed
    "Details" button and stays out of the way until opened.
    """
    org_link = f'<a href="{prefix}orgs/{esc(a["slug"])}.html">{esc(a["org_name"])}</a>'
    out = [
        "<article>",
        f'<h2><a href="{esc(a["url"])}">{esc(a["title"])}</a></h2>',
        f"<p>{org_link} &middot; "
        f'<a href="{esc(a["url"])}">Read at {esc(a["org_name"])}</a></p>',
        "<details><summary>Details</summary><dl>",
    ]
    when = local_dateline(a["published_at"] or a["fetched_at"], a.get("state"), a.get("timezone"))
    where = a.get("beat") or a.get("city") or a.get("coverage") or ""
    if a.get("author"):
        out.append(f"<dt>Reported by</dt><dd>{esc(a['author'])}</dd>")
        # What the full edition puts behind a marker beside the byline is
        # written out here as a sentence instead — same words, no marker.
        who = reporter_of(a)
        if who:
            out.append(f"<dt>About {esc(who['name'])}</dt>"
                       f"<dd>{esc(reporter_facts(who))}</dd>")
    if when:
        out.append(f"<dt>Published</dt><dd>{esc(when)}</dd>")
    if where:
        out.append(f"<dt>Covers</dt><dd>{esc(where)}</dd>")
    if a.get("subject"):
        out.append(f"<dt>Subject</dt><dd>{esc(a['subject'])}</dd>")
    tags = ", ".join(ownership_tags(a))
    if tags:
        out.append(f"<dt>Newsroom type</dt><dd>{esc(tags)}</dd>")
    # What the full edition puts behind a disclosure marker beside the
    # masthead is written out here instead; a marker with nothing behind it
    # would be worse than no marker at all.
    about = about_opening(a.get("about_text"))
    if about:
        out.append(f"<dt>About {esc(a['org_name'])}</dt><dd>{esc(about)}</dd>")
    if a.get("image_alt"):
        out.append(f"<dt>Picture</dt><dd>{esc(a['image_alt'])}</dd>")
    if a.get("summary"):
        out.append(f"<dt>Summary</dt><dd>{esc(a['summary'][:600])}</dd>")
    # Only where there is a payment page. The homepage is not one, and a
    # screen reader has even less to go on than a sighted reader when a link
    # called "Support" lands on a masthead.
    if a.get("support_url"):
        label = a.get("support_label") or "Donate"
        out.append(
            f'<dt>Support</dt><dd><a href="{esc(a["support_url"])}">'
            f'{esc(label)} {esc(a["org_name"])}</a></dd>'
        )
    out.append("</dl></details></article>")
    return "\n".join(out)


def write_text_edition(site, cur, articles, orgs):
    """A no-image, no-JavaScript edition built for screen readers."""
    out = site / "text"
    out.mkdir(parents=True, exist_ok=True)
    body = [
        f"<h1>{esc(config.SITE_NAME)}</h1>",

    ]
    body += [render_text_item(a) for a in articles[:60]]
    (out / "index.html").write_text(text_page(f"{config.SITE_NAME} — plain text", "\n".join(body)))

    # One page per state: 1,924 newsrooms in a single document is a quarter
    # of a megabyte, which is exactly what a plain edition should not be.
    groups, national = group_orgs_by_state(orgs)

    def rows_for(group):
        rows = ["<ul>"]
        for org in group:
            support = org.get("support_url")
            tail = (f' · <a href="{esc(support)}">{esc(org.get("support_label") or "Support")}</a>'
                    if support else "")
            rows.append(
                f'<li><a href="../orgs/{esc(org["slug"])}.html">{esc(org["name"])}</a>'
                f' — {esc(org["coverage"] or place_label(org))}{tail}</li>'
            )
        rows.append("</ul>")
        return "".join(rows)

    index = [f"<h1>Newsrooms</h1><p>{len(orgs)} newsrooms. Choose a state.</p><ul>"]
    for state_name, group in groups.items():
        slug = re.sub(r"[^a-z0-9]+", "-", state_name.lower()).strip("-")
        index.append(f'<li><a href="{slug}.html">{esc(state_name)}</a> ({len(group)})</li>')
        (out / f"{slug}.html").write_text(text_page(
            f"{config.SITE_NAME} — {state_name}, plain text",
            f"<h1>{esc(state_name)}</h1><p>{len(group)} newsrooms. "
            f'<a href="catalog.html">All states</a></p>' + rows_for(group)))
    index.append("</ul>")
    if national:
        index.append("<h2>Everywhere</h2>" + rows_for(national))
    (out / "catalog.html").write_text(
        text_page(f"{config.SITE_NAME} — newsrooms, plain text", "".join(index))
    )


def render_org_page(cur, org):
    parts = [f'<h1><a href="{esc(org["url"])}">{esc(org["name"])}</a></h1>', f"<p>{meta_line(org)}</p>"]
    tags = tag_links(org)
    if tags:
        parts.append(f"<p><small>{tags}</small></p>")
    if org.get("support_url"):
        label = org.get("support_label") or "Support"
        parts.append(f'<p><a href="{esc(org["support_url"])}"><strong>{esc(label)}</strong></a></p>')
    if org["feed_url"]:
        parts.append(f'<p><a href="{esc(org["feed_url"])}">RSS feed</a></p>')
    if usable_about(org["about_text"]):
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


# How many recent stories to look for cross-state echoes from.
CONNECTION_ANCHORS = int(os.environ.get("CONNECTION_ANCHORS", "400"))
