"""Generate the static site from the database: plain HTML, no CSS, no JS.

Pages:
    site/index.html         the feed itself, newest first (page 1)
    site/catalog.html       every org, in their own words, grouped by state
    site/map.html           inline-SVG coverage map (Albers projection)
    site/feed-2.html ...     the rest of the feed
    site/connections.html   strongest story pairs across regions (pgvector)
    site/orgs/<slug>.html   one page per org
    site/onepage.html       everything on one self-contained page
    data/catalog.json       machine-readable catalog export
"""

import collections
import json
import shutil
import math
import os
import re
from datetime import datetime, timezone
from html import escape as esc

from . import config
from .albers import MapProjection
from .timezones import local_dateline, local_time
from . import filters, syndicate
from .tags import TAG_GROUPS, region_of, tag_slug
from .db import connect

MIN_RELATED_SIM = float(os.environ.get("MIN_RELATED_SIM", "0.30"))
# Above this cosine similarity, or with near-identical headlines, two
# articles are the same story running in multiple outlets (syndication or a
# co-publish), not two newsrooms independently circling one topic.
SAME_STORY_SIM = float(os.environ.get("SAME_STORY_SIM", "0.80"))
FEED_PAGE_ARTICLES = 600
# Items per feed page — the rest arrive as you scroll.
FEED_PAGE_SIZE = 30
# An image on this many articles is house art, not story art.
HOUSE_IMAGE_USES = 4
# How many recent stories to look for cross-state echoes from.
CONNECTION_ANCHORS = int(os.environ.get("CONNECTION_ANCHORS", "400"))
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

# Subjects come first because they are what a reader is actually choosing
# between; then the ways of navigating the whole thing; then the meta pages.
SUBJECT_ORDER = [
    "News", "Politics", "Opinion", "Health", "Environment", "Education",
    "Business", "Housing", "Sports", "Food", "Arts",
]
NAV_BROWSE = [
    ("map.html", "Map"),
    ("catalog.html", "Newsrooms"),
    ("big-stories.html", "Big Stories"),
    ("story-links.html", "Story Links"),
]
NAV_META = [
    ("resources.html", "Resources"),
    ("text/", "Plain text edition"),
    ("feeds.html", "RSS Feeds"),
    ("about.html", "About This Site"),
]
NAV = NAV_BROWSE + NAV_META


# Everything the menu offers, filled in by main() before anything renders.
MENU_SUBJECTS = []
MENU_FEEDS = []


def stylesheet(prefix=""):
    """One small stylesheet. Type does the work; there is almost no chrome."""
    return f"""<style>
@font-face{{font-family:Plex;src:url({prefix}fonts/ibm-plex-sans.woff2) format('woff2');
font-weight:100 700;font-display:swap}}
@font-face{{font-family:PlexMono;src:url({prefix}fonts/ibm-plex-mono.woff2) format('woff2');
font-weight:400;font-display:swap}}
@font-face{{font-family:PlexSerif;src:url({prefix}fonts/ibm-plex-serif.woff2) format('woff2');
font-weight:400;font-display:swap}}
@font-face{{font-family:PlexSerif;src:url({prefix}fonts/ibm-plex-serif-600.woff2) format('woff2');
font-weight:600;font-display:swap}}
:root{{--fg:#111;--bg:#fff;--dim:#555;--rule:#ddd;--link:#c8102e;--seen:#8c0b20;
--band:#f6f6f4}}
@media(prefers-color-scheme:dark){{
:root{{--fg:#e8e8e8;--bg:#111;--dim:#a6a6a6;--rule:#333;--link:#ff6b6b;--seen:#cf8f8f;
--band:#181818}}}}
html{{-webkit-text-size-adjust:100%}}
body{{font:400 1.0625rem/1.6 PlexSerif,Georgia,serif;color:var(--fg);background:var(--bg);
max-width:40rem;margin:0 auto;padding:1rem 0 4rem;overflow-wrap:break-word}}
header,main>h1,main>p,main>ul,main>h2,main>h3,main>figure,footer,#feed-items>p,
main>form,main>table,main>blockquote,main>hr,main>nav,main>div>h1,main>div>h2
{{padding-left:1rem;padding-right:1rem}}
h1,h2,h3,.sans{{font-family:Plex,system-ui,sans-serif}}
a{{color:var(--link);text-decoration:none}}
a:visited{{color:var(--seen)}}
a:hover,a:focus{{text-decoration:underline}}
:focus-visible{{outline:3px solid var(--link);outline-offset:2px}}
.skip{{position:absolute;left:-9999px}}
.skip:focus{{position:static;display:block;padding:.5rem 0}}
h1{{font-size:1.5rem;line-height:1.2;margin:1rem 0}}
h2{{font-size:1.2rem;line-height:1.25;margin:.2rem 0 .4rem}}
article h2{{font-family:PlexSerif,Georgia,serif;font-size:1.7rem;font-weight:400;
line-height:1.2;margin:.35rem 0 .4rem;max-width:75%}}
@media(max-width:34rem){{article h2{{max-width:100%;font-size:1.5rem}}}}
h3{{font-size:1rem;margin:1.2rem 0 .4rem}}
p{{margin:0 0 .7rem}}
ul{{padding-left:1.1rem}}
li{{margin-bottom:.4rem}}
.meta{{font:400 .8rem/1.4 PlexMono,ui-monospace,monospace;color:var(--dim)}}
.meta a{{color:var(--link)}}
article{{padding:1.5rem 1rem;border-bottom:1px solid var(--rule)}}
/* Alternate a very light tint so the eye can find where one story
   ends and the next begins while scanning. */
#feed-items>article:nth-child(even){{background:var(--band)}}
@media(max-width:34rem){{article{{padding:1.5rem .75rem}}}}
article::after{{content:"";display:block;clear:both}}
img{{max-width:100%;height:auto;display:block}}
.shot{{float:left;width:33%;margin:.35rem 1rem .3rem 0}}
.shot img{{width:100%}}
@media(max-width:34rem){{.shot{{width:40%}}}}
time[data-pub]{{cursor:pointer;border-bottom:1px dotted var(--rule)}}
.yours{{color:var(--dim)}}
.lozenge{{display:inline-block;font:400 .72rem/1 PlexMono,ui-monospace,monospace;
padding:.25rem .5rem;margin:0 .3rem .3rem 0;border:1px solid var(--rule);border-radius:1rem;
color:var(--dim);text-decoration:none}}
.lozenge:hover,.lozenge:focus{{border-color:var(--link);color:var(--link);text-decoration:none}}
.lozenge[aria-current=page],.lozenge.on{{border-color:var(--link);color:var(--bg);background:var(--link)}}
.chips{{margin:.75rem 0 1.25rem}}
.mapwrap{{position:relative;margin:0 0 1rem}}
.mapwrap a[data-slug]{{cursor:pointer}}
/* Half the width of the map, centred over it, sitting low so the dots
   stay visible above the panel. */
.preview{{position:absolute;left:50%;transform:translateX(-50%);
width:50%;min-width:min(17rem,88%);bottom:1.6rem;background:var(--bg);
border:2px solid var(--fg);padding:.85rem 1rem;max-height:82%;overflow:auto}}
.preview ul{{list-style:none;padding:0;margin:.4rem 0}}
.preview li{{margin:0 0 .55rem}}
.preview-close{{float:right;border:0;background:none;font-size:1.4rem;line-height:1;
padding:0 0 0 .5rem;color:var(--fg);cursor:pointer}}
/* Section, then what kind of newsroom this is, then the ask — a column
   down the right of each story. */
.tagcol{{float:right;width:32%;max-width:10rem;margin:.15rem 0 .6rem .9rem;
display:flex;flex-direction:column;align-items:flex-end;gap:.3rem}}
.tagcol .lozenge{{margin:0}}
.tagcol .section{{border-color:var(--fg);color:var(--fg);font-weight:600}}
.lozenge.give{{border-color:var(--link);color:var(--link);font-weight:600}}
.lozenge.give:hover,.lozenge.give:focus{{background:var(--link);color:var(--bg)}}
a.lozenge.more{{white-space:nowrap;border-color:var(--fg);color:var(--fg);font-weight:600}}
a.lozenge.more:hover,a.lozenge.more:focus{{background:var(--fg);color:var(--bg)}}
.source{{font-family:Plex,system-ui,sans-serif;font-size:1rem;margin:0 0 .25rem}}
.whenwhere{{font:400 .8rem/1.4 PlexMono,ui-monospace,monospace;color:var(--dim);margin:0 0 .15rem}}
.byline{{font-family:Plex,system-ui,sans-serif;font-size:.9rem;color:var(--dim);margin:0 0 .6rem}}
@media(max-width:34rem){{.tagcol{{width:38%;max-width:8.5rem}}}}
svg{{max-width:100%;height:auto}}
blockquote{{margin:0 0 .7rem;padding-left:.9rem;border-left:3px solid var(--rule)}}
hr{{border:0;border-top:1px solid var(--rule);margin:2rem 0}}
input,button{{font:inherit;font-size:1rem;padding:.4rem .6rem;color:var(--fg);
background:var(--bg);border:1px solid var(--rule)}}
input[type=search]{{width:min(20rem,68%)}}
button{{cursor:pointer;color:var(--link);font-size:1.15rem;line-height:1;padding:.42rem .7rem}}
/* The menu is the whole navigation: sections, subjects, feeds, search.
   Pinned to the top so it is reachable anywhere down an endless feed. */
header{{position:sticky;top:0;z-index:10;background:var(--bg);display:grid;
grid-template-columns:auto 1fr auto;align-items:center;gap:.75rem;
border-bottom:2px solid var(--fg);margin-bottom:1.25rem}}
.home{{grid-column:2;justify-self:center;display:block;padding:.5rem 0;min-width:0;
max-width:22rem;width:100%}}
.menu{{grid-column:1;grid-row:1;justify-self:start}}
/* keeps the masthead optically centred against the burger on the left */
header::after{{content:"";grid-column:3;width:28px}}
.menu>summary{{cursor:pointer;list-style:none;padding:.55rem 0;
display:flex;gap:.6rem;align-items:center}}
.menu>summary::-webkit-details-marker{{display:none}}
.menu{{flex:none}}
.burger{{width:28px;height:28px;flex:none;fill:currentColor;display:block}}
.menu>summary{{color:var(--fg)}}
.menu>summary:hover,.menu>summary:focus{{color:var(--link)}}

.burger .cross{{display:none}}
.menu[open] .burger .bars{{display:none}}
.menu[open] .burger .cross{{display:inline}}
.masthead{{width:100%;height:auto;display:block}}
.panel{{position:absolute;top:100%;left:0;width:50%;min-width:min(16rem,88%);
background:var(--bg);border:2px solid var(--fg);border-top:0;
padding:.75rem 1rem 1rem;max-height:78vh;overflow-y:auto;z-index:20}}
.panel hr{{border:0;border-top:1px solid var(--rule);margin:.85rem 0}}
.panel p{{margin:0}}
.panel ul.cols{{display:grid;grid-template-columns:repeat(auto-fill,minmax(7rem,1fr));gap:.4rem}}
.panel h3{{font:400 .8rem/1.4 PlexMono,ui-monospace,monospace;color:var(--dim);
margin:1rem 0 .3rem;text-transform:uppercase;letter-spacing:.06em}}
.panel>nav:first-of-type ul{{display:block}}
.panel>nav:first-of-type li{{margin:0 0 .45rem}}
.panel>nav:first-of-type a{{font-size:1.05rem;font-weight:600}}
.panel ul{{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:.3rem .9rem}}
.panel li{{margin:0}}
</style>"""


def menu(prefix="", site_name=""):
    """The masthead links home; the burger opens everything else.

    The masthead sits outside the <summary> deliberately — a link inside a
    disclosure summary toggles the disclosure instead of following the link
    in several browsers, which is exactly the wrong thing for the way home.
    """
    def href(target):
        return target if target.startswith("/") else prefix + target

    ordered = [(name, slug) for name in SUBJECT_ORDER
               for n, slug in MENU_SUBJECTS if n == name]
    ordered += [(n, slug) for n, slug in MENU_SUBJECTS if n not in SUBJECT_ORDER]
    subjects = "".join(
        f'<li><a href="{prefix}subjects/{slug}.html">{esc(name)}</a></li>'
        for name, slug in ordered
    )
    # Horizontal rows read better separated by middots than by whitespace.
    browse = " \u00b7 ".join(f'<a href="{href(t)}">{esc(label)}</a>'
                             for t, label in NAV_BROWSE)
    meta = " \u00b7 ".join(f'<a href="{href(t)}">{esc(label)}</a>'
                           for t, label in NAV_META)

    return f"""<a class="home" href="{prefix}index.html"><img class="masthead"
 src="{prefix}masthead.svg" alt="{esc(site_name)}" width="440" height="44"></a>
<details class="menu">
<summary title="Menu"><svg class="burger" viewBox="0 0 24 24" role="img" aria-label="Menu"
 width="24" height="24" aria-hidden="true" focusable="false"><g class="bars"><rect x="1" y="4"
 width="22" height="2.5" rx="1.25"/><rect x="1" y="10.75" width="22" height="2.5" rx="1.25"/><rect
 x="1" y="17.5" width="22" height="2.5" rx="1.25"/></g><g class="cross"><rect x="1" y="10.75"
 width="22" height="2.5" rx="1.25" transform="rotate(45 12 12)"/><rect x="1" y="10.75" width="22"
 height="2.5" rx="1.25" transform="rotate(-45 12 12)"/></g></svg></summary>
<div class="panel">
<form role="search" action="/search" method="get">
<p><label class="skip" for="q">Search</label>
<input type="search" id="q" name="q" placeholder="Search headlines and summaries">
<button type="submit" aria-label="Search">&rarr;</button></p>
</form>
<hr>
<nav aria-label="Subjects"><ul class="cols">{subjects}</ul></nav>
<hr>
<nav aria-label="Browse"><p>{browse}</p></nav>
<hr>
<nav aria-label="About"><p>{meta}</p></nav>
<p class="meta"><a href="/admin" rel="nofollow">Manage feeds</a></p>
</div>
</details>"""


def footer_links(prefix=""):
    """The same routes the menu offers, laid flat at the foot of the page."""
    def href(target):
        return target if target.startswith("/") else prefix + target
    items = [(prefix + "index.html", "Today's news")] + NAV_BROWSE + NAV_META
    return " \u00b7 ".join(
        f'<a href="{href(t) if not t.startswith(prefix) else t}">{esc(label)}</a>'
        for t, label in items
    )


def page(title, body, prefix="", nav_html=None, scripts="", description="",
         feed_href="feed.xml", feed_title=None):
    meta_desc = (
        f'<meta name="description" content="{esc(description)}">\n' if description else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
{meta_desc}<link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">
<link rel="alternate" type="application/rss+xml"
 title="{esc(feed_title or config.SITE_NAME)}" href="{prefix}{feed_href}">
{stylesheet(prefix)}
</head>
<body>
<a class="skip" href="#main">Skip to the stories</a>
<header>
{nav_html or menu(prefix, config.SITE_NAME)}
</header>
<main id="main">
{body}
</main>
<footer>
<hr>
<nav aria-label="Site"><p class="meta">{footer_links(prefix)}</p></nav>
</footer>
{MENU_SCRIPT}
{MAP_SCRIPT}
{LOCAL_TIME_SCRIPT}
{scripts}
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
        f"<p>{len(orgs)} newsrooms. Text quoted from their own About pages.</p>",
    ]
    for state_name, group in groups.items():
        parts.append(f"<h2>{esc(state_name)}</h2>")
        parts.extend(catalog_entry(o, mode, prefix) for o in group)
    if national:
        parts.append("<h2>Everywhere (no fixed geography)</h2>")
        parts.extend(catalog_entry(o, mode, prefix) for o in national)
    return "\n".join(parts)


def render_result_map(orgs, prefix="", caption="Where these newsrooms are", stories=None):
    """A compact map of one subset of newsrooms — used above search results."""
    proj = MapProjection(config.STATES_GEOJSON)
    mappable = [o for o in orgs if o.get("lat") and o.get("lon") and o.get("state")]
    if not mappable:
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
    return (
        f'<figure class="mapwrap">'
        f'<svg viewBox="0 0 {proj.width} {proj.height}" width="100%" role="img" '
        f'aria-label="{esc(caption)}: {esc(names[:600])}">{states}{"".join(dots)}</svg>'
        f'<div class="preview" hidden><button type="button" class="preview-close" '
        f'aria-label="Close">&times;</button><div class="preview-body"></div></div>'
        f'<figcaption class="meta">{esc(caption)} &mdash; {len(mappable)} newsrooms. '
        f'Tap a dot for its stories.</figcaption>'
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


def search_form(query=""):
    """Plain GET form — search works with JavaScript switched off."""
    return (
        '<form action="/search" method="get" role="search">'
        f'<p><input type="search" name="q" value="{esc(query)}" '
        'placeholder="Search headlines and summaries" aria-label="Search"> '
        '<button type="submit" aria-label="Search">&rarr;</button></p></form>'
    )


def collapse_duplicates(articles):
    """Fold reprints of one story into a single feed entry.

    Syndication and co-publishing mean the same headline arrives from
    several newsrooms; showing it four times makes the feed look broken.
    The first copy (newest, since the list is already ordered) is kept and
    the rest are listed under it as "Also in". Headline-token overlap
    decides — the same measure classify_pair() uses for reprints.
    """
    kept = []
    for a in articles:
        tokens = title_tokens(a["title"])
        match = None
        if len(tokens) >= 4:
            for candidate in kept:
                other = candidate["_tokens"]
                union = tokens | other
                if not union or len(other) < 4:
                    continue
                if len(tokens & other) / len(union) >= 0.75:
                    match = candidate
                    break
        if match:
            match["_also"].append(a)
        else:
            entry = dict(a)
            entry["_tokens"] = tokens
            entry["_also"] = []
            kept.append(entry)
    return kept


MAP_SCRIPT = """<script>
/* Clicking a dot opens that newsroom's matching stories over the map.
   With JavaScript off the dot stays an ordinary link to the newsroom. */
(function () {
  var wrap = document.querySelector(".mapwrap");
  if (!wrap) return;
  var data = document.getElementById("map-stories");
  if (!data) return;
  var stories;
  try { stories = JSON.parse(data.textContent); } catch (e) { return; }
  var panel = wrap.querySelector(".preview");
  var body = wrap.querySelector(".preview-body");

  wrap.addEventListener("click", function (e) {
    if (e.target.closest(".preview-close")) { panel.hidden = true; return; }
    var dot = e.target.closest("a[data-slug]");
    if (!dot) return;
    var found = stories[dot.getAttribute("data-slug")];
    if (!found) return;
    e.preventDefault();
    var html = "<p class='source'><strong>" + found.name + "</strong></p><ul>";
    for (var i = 0; i < found.items.length; i++) {
      var it = found.items[i];
      html += "<li><a href='" + it.url + "'>" + it.title + "</a>" +
              "<br><span class='meta'>" + it.when + "</span></li>";
    }
    html += "</ul><p><a class='lozenge' href='" + found.site + "'>Visit " +
            found.name + "</a>";
    if (found.support) {
      html += "<a class='lozenge give' href='" + found.support + "'>" +
              found.supportLabel + "</a>";
    }
    html += "</p>";
    body.innerHTML = html;
    panel.hidden = false;
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") panel.hidden = true;
  });
})();
</script>"""

MENU_SCRIPT = """<script>
/* A disclosure stays open until it is told otherwise; a menu should not. */
(function () {
  function close(e) {
    var open = document.querySelector("details.menu[open]");
    if (!open) return;
    if (e.type === "keydown") {
      if (e.key === "Escape") { open.removeAttribute("open"); }
      return;
    }
    if (!open.contains(e.target)) { open.removeAttribute("open"); }
  }
  document.addEventListener("click", close);
  document.addEventListener("keydown", close);
})();
</script>"""

LOCAL_TIME_SCRIPT = """<script>
/* Datelines are the newsroom's own local time. Tap one to see that moment
   in your time zone; tap again to put it away. Delegated from the document
   so items added by infinite scroll work without rebinding. */
document.addEventListener("click", function (event) {
  var el = event.target.closest && event.target.closest("time[data-pub]");
  if (!el) return;
  var open = el.nextElementSibling;
  if (open && open.className === "yours") { open.remove(); return; }
  var when = new Date(el.getAttribute("datetime"));
  if (isNaN(when.getTime())) return;
  var mine;
  try {
    mine = new Intl.DateTimeFormat(undefined, {
      hour: "numeric", minute: "2-digit", timeZoneName: "short"
    }).format(when);
  } catch (e) { return; }
  var span = document.createElement("span");
  span.className = "yours";
  span.textContent = " \u00b7 " + mine + " your time";
  el.parentNode.insertBefore(span, el.nextSibling);
});
</script>"""

FEED_SCRIPT = """<script>
/* Progressive enhancement only: without JS the More link is an ordinary
   link to the next page, and every page stands on its own. */
(function () {
  var link = document.getElementById("more");
  var items = document.getElementById("feed-items");
  if (!link || !items || !window.IntersectionObserver || !window.fetch) return;
  var busy = false;
  var io = new IntersectionObserver(function (entries) {
    if (!entries[0].isIntersecting || busy) return;
    busy = true;
    fetch(link.href).then(function (r) { return r.text(); }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var incoming = doc.getElementById("feed-items");
      if (incoming) {
        while (incoming.firstChild) items.appendChild(incoming.firstChild);
      }
      var next = doc.getElementById("more");
      if (next) { link.href = next.getAttribute("href"); busy = false; }
      else { io.disconnect(); link.parentNode.remove(); }
    }).catch(function () { busy = false; });
  }, { rootMargin: "600px" });
  io.observe(link);
})();
</script>"""


def feed_page_name(stem, index):
    """feed.html, feed-2.html, feed-3.html ..."""
    return f"{stem}.html" if index == 0 else f"{stem}-{index + 1}.html"


def write_feed_pages(site, cur, articles, stem, title, heading, prefix="",
                     subject_nav=None, skip_images=(), subdir=None,
                     first_name=None, intro="", with_related=True,
                     feed_href="feed.xml", feed_title=None, show_heading=True):
    """Split a feed into pages so no single page carries the whole crawl."""
    target = (site / subdir) if subdir else site
    target.mkdir(parents=True, exist_ok=True)
    chunks = [articles[i:i + FEED_PAGE_SIZE] for i in range(0, len(articles), FEED_PAGE_SIZE)] or [[]]
    for index, chunk in enumerate(chunks):
        nav = subject_nav if index == 0 else None
        body = render_feed(cur, chunk, prefix=prefix, heading=heading,
                           subject_nav=nav, skip_images=skip_images,
                           page_index=index, page_count=len(chunks), stem=stem,
                           intro=intro, with_related=with_related,
                           show_heading=show_heading)
        head = title if index == 0 else f"{title} — page {index + 1}"
        name = first_name if (index == 0 and first_name) else feed_page_name(stem, index)
        target.joinpath(name).write_text(
            page(head, body, prefix=prefix, scripts=FEED_SCRIPT,
                 feed_href=feed_href, feed_title=feed_title)
        )
    return len(chunks)


def subject_href(subject, prefix=""):
    return f"{prefix}subjects/{subject.lower().replace(' ', '-')}.html"


def support_link(article):
    """Every item carries the ask. Falls back to the newsroom's front page."""
    if article.get("support_url"):
        url = article["support_url"]
        label = article.get("support_label") or "Donate"
    else:
        # No payment page found — send them to the newsroom itself rather
        # than label a homepage as something it is not.
        url, label = article["org_url"], "Support"
    return f'<a class="lozenge give" href="{esc(url)}">{esc(label)}</a>'


_EM_SPACES = re.compile(r"\s*\u2014\s*")


def tighten(text):
    """Close the gaps around em dashes.

    ' \u2014 ' gives the browser two break opportunities and a wide gap that
    reads as a hole in a headline. Closed up, the dash stays with the words
    on either side of it.
    """
    return _EM_SPACES.sub("\u2014", text or "")


def place_line(a, mode="site", prefix=""):
    """State / region / publication — or National / beat / publication for
    the outlets organised around a subject rather than a place."""
    if (a.get("coverage_type") or "") == "national":
        first = "National"
    else:
        first = a.get("state") or "National"
    # Topic-driven outlets name their beat where a local paper names its city.
    middle = a.get("beat") or a.get("city")
    if not middle:
        middle = "Statewide" if (a.get("coverage_type") or "") == "state" else None
    # Straight to the newsroom. Our own page about them is reachable from
    # the catalog; from the feed, a reader wants the publication itself.
    pub = f'<a href="{esc(a["org_url"])}">{esc(a["org_name"])}</a>'
    return " / ".join(esc(part) for part in (first, middle) if part) + " / " + pub


def render_feed_item(cur, a, mode="site", prefix="", with_related=True, skip_images=()):
    """Source and section, then how to support them, then when and where,
    then the story."""
    out = ["<article>"]

    # 1. a column down the right: section first, then what kind of newsroom
    #    this is, then the ask.
    column = []
    if a.get("subject"):
        label = esc(a["subject"])
        column.append(f'<span class="lozenge section">{label}</span>' if mode == "onepage"
                      else f'<a class="lozenge section" '
                           f'href="{subject_href(a["subject"], prefix)}">{label}</a>')
    column.append(tag_links(a, prefix if mode != "onepage" else ""))
    column.append(support_link(a))
    out.append(f'<aside class="tagcol">{"".join(column)}</aside>')

    # 2. source, bold and linked
    source = f'<a href="{esc(a["org_url"])}"><strong>{esc(a["org_name"])}</strong></a>'
    out.append(f'<p class="source">{source}</p>')

    # 3. when and where
    moment = a["published_at"] or a["fetched_at"]
    dateline = local_dateline(moment, a.get("state"), a.get("timezone"))
    pub_time = local_time(moment, a.get("state"), a.get("timezone"))
    where = a.get("beat") or a.get("city")
    state_name = STATE_NAMES.get((a.get("state") or "").upper())
    if (a.get("coverage_type") or "") == "national":
        place = "National" + (f"/{where}" if where else "")
    else:
        place = "/".join(p for p in (state_name, where) if p) or "National"
    when = (f'<time datetime="{moment.astimezone(timezone.utc).isoformat()}" '
            f'data-pub="{esc(pub_time)}">{esc(dateline)}</time>') if dateline else ""
    out.append(f'<p class="whenwhere">{when}{" &middot; " if when else ""}{esc(place)}</p>')

    # 4. headline, 5. byline
    out.append(f'<h2><a href="{esc(a["url"])}">{esc(tighten(a["title"]))}</a></h2>')
    if a.get("author"):
        out.append(f'<p class="byline">By {esc(a["author"])}</p>')

    if a.get("image_file") and a["image_file"] not in skip_images:
        size = ""
        if a.get("image_w") and a.get("image_h"):
            size = f' width="{a["image_w"]}" height="{a["image_h"]}"'
        alt = esc(a.get("image_alt") or "")
        out.append(
            f'<a class="shot" href="{esc(a["url"])}" tabindex="-1" aria-hidden="true">'
            f'<img src="{prefix}img/{esc(a["image_file"])}" alt="{alt}"{size} '
            f'loading="lazy" decoding="async"></a>'
        )

    # 6. the text, with Read more running on from the end of it
    summary = esc(tighten(a["summary"][:400])) if a.get("summary") else ""
    more = (f'<a class="lozenge more" href="{esc(a["url"])}">Read more '
            f'<span aria-hidden="true">&rarr;</span></a>')
    out.append(f"<p>{summary} {more}</p>" if summary else f"<p>{more}</p>")

    if a.get("_also"):
        others = " &middot; ".join(
            f'<a href="{esc(d["url"])}">{esc(d["org_name"])}</a>' for d in a["_also"]
        )
        out.append(f'<p class="meta">Also in {others}</p>')
    if with_related:
        echoes = []
        for r_title, r_url, r_org, r_slug, r_org_url, r_sim in related_to(cur, a["id"]):
            if classify_pair(r_sim, a["title"], r_title) == "kindred" and len(echoes) < 2:
                echoes.append(f'<a href="{esc(r_url)}">{esc(r_org)}: {esc(tighten(r_title))}</a>')
        if echoes:
            out.append(f'<p class="meta">Echo: {" &middot; ".join(echoes)}</p>')

    out.append("</article>")
    return "\n".join(out)


def render_feed(cur, articles, mode="site", prefix="", with_related=True, heading="Feed",
                subject_nav=None, skip_images=(), page_index=0, page_count=1, stem=None,
                intro="", show_heading=True):
    parts = []
    if page_index == 0:
        if show_heading:
            parts.append(f"<h1>{esc(heading)}</h1>")
        if intro:
            parts.append(intro)
        if subject_nav:
            parts.append(subject_nav)
    parts.append('<div id="feed-items">')
    for a in articles:
        parts.append(render_feed_item(cur, a, mode, prefix, with_related, skip_images))
    parts.append("</div>")
    if stem and page_index + 1 < page_count:
        parts.append(
            f'<p><a id="more" href="{feed_page_name(stem, page_index + 1)}">'
            f'More stories</a></p>'
        )
    return "\n".join(parts)


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


def _org_line(art, mode, prefix):
    loc = STATE_NAMES.get(art["state"], art["state"]) if art["state"] else "everywhere"
    href = art["org_url"] if mode == "onepage" else f"{prefix}orgs/{art['slug']}.html"
    return f'<a href="{esc(href)}">{esc(art["org_name"])}</a> ({esc(loc)})'


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


# What a reader actually wants to know about a newsroom: who owns it and
# who it serves. The `model` field is free text written per newsroom, so it
# is matched to a small canonical set rather than printed raw.
MODEL_TAGS = [
    (r"co-?operative|\bco-?op\b", "Co-op"),
    (r"worker-owned|worker-led|employee-owned", "Worker-owned"),
    (r"journalist-owned|journalist-founded|writer-owned", "Journalist-owned"),
    (r"non-?profit|501\(c\)", "Nonprofit"),
    (r"public media|public radio|public broadcast", "Public media"),
    (r"public benefit corp", "Public benefit corp"),
    (r"\bfamily\b", "Family-owned"),
    (r"native-owned|tribal", "Native-owned"),
    (r"college-based|student", "College"),
    (r"newsletter", "Newsletter"),
    (r"reader-funded|member-supported|reader-supported", "Reader-funded"),
    (r"independent", "Independent"),
]


def ownership_tags(org):
    """Canonical tags for one newsroom: ownership first, then community."""
    tags = []
    model = (org.get("model") or "").lower()
    for pattern, label in MODEL_TAGS:
        if re.search(pattern, model) and label not in tags:
            tags.append(label)
    for feature in (org.get("features") or []):
        if feature not in tags:
            tags.append(feature)
    return tags


def tag_links(org, prefix=""):
    """Tags as tappable lozenges, each leading to that tag's own feed."""
    out = [
        f'<a class="lozenge" href="{prefix}tags/{tag_slug(tag)}.html">{esc(tag)}</a>'
        for tag in ownership_tags(org)
    ]
    return "".join(out)


def feature_href(feature, prefix=""):
    return f"{prefix}features/{re.sub(r'[^a-z0-9]+', '-', feature.lower()).strip('-')}.html"


def state_href(state_name, prefix=""):
    return f"{prefix}catalog/{re.sub(r'[^a-z0-9]+', '-', state_name.lower()).strip('-')}.html"


def feature_links(org, prefix=""):
    return " · ".join(
        f'<a href="{feature_href(f, prefix)}">{esc(f)}</a>' for f in (org.get("features") or [])
    )


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
for each tag; a <a href="text/index.html">plain text edition</a> with no
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
    feature_counts = collections.Counter(t for o in orgs for t in ownership_tags(o))
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


# Plenty of About pages scrape down to a cookie notice or "we have turned
# off comments". Show the quotation only when there is really something there.
_ABOUT_JUNK = re.compile(
    r"turned off comments|comment(ing)? (is|has been) (disabled|closed)|"
    r"cookies?|privacy policy|javascript|subscribe to (our|the) newsletter|"
    r"page not found|404", re.IGNORECASE)


def usable_about(text):
    text = (text or "").strip()
    if len(text) < 240:
        return False
    head = text[:400]
    return not _ABOUT_JUNK.search(head)


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
summary{cursor:pointer;font-size:.95rem}
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
<a href="../index.html">Full version</a></p>
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
    if when:
        out.append(f"<dt>Published</dt><dd>{esc(when)}</dd>")
    if where:
        out.append(f"<dt>Covers</dt><dd>{esc(where)}</dd>")
    if a.get("subject"):
        out.append(f"<dt>Subject</dt><dd>{esc(a['subject'])}</dd>")
    tags = ", ".join(ownership_tags(a))
    if tags:
        out.append(f"<dt>Newsroom type</dt><dd>{esc(tags)}</dd>")
    if a.get("image_alt"):
        out.append(f"<dt>Picture</dt><dd>{esc(a['image_alt'])}</dd>")
    if a.get("summary"):
        out.append(f"<dt>Summary</dt><dd>{esc(a['summary'][:600])}</dd>")
    support_url = a.get("support_url") or a["org_url"]
    support_label = a.get("support_label") or "Support"
    out.append(
        f'<dt>Support</dt><dd><a href="{esc(support_url)}">'
        f'{esc(support_label)} {esc(a["org_name"])}</a></dd>'
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


ORG_COLUMNS = (
    "id", "slug", "name", "url", "about_url", "feed_url", "city", "state", "lat", "lon",
    "coverage", "coverage_type", "model", "affiliations", "founded",
    "support_url", "support_label", "features", "source", "tagline", "beat",
    "about_text", "about_source_url", "about_fetched_at",
)


def load_orgs(cur):
    cur.execute(f"SELECT {', '.join(ORG_COLUMNS)} FROM orgs ORDER BY name")
    return [dict(zip(ORG_COLUMNS, row)) for row in cur.fetchall()]


def load_articles(cur, limit, subject=None, feature=None, default_only=False,
                  apply_filters=True):
    filter_sql, filter_params = filters.where_clause(cur) if apply_filters else ("", [])
    cur.execute(
        """
        SELECT a.id, a.url, a.title, a.summary, a.author, a.published_at, a.fetched_at,
               a.image_file, a.image_w, a.image_h, a.image_alt, a.subject,
               o.name AS org_name, o.slug, o.url AS org_url,
               o.support_url, o.support_label,
               o.state, o.city, o.beat, o.coverage, o.coverage_type,
               o.timezone, o.model, o.features, o.feed_url, o.in_default, o.language
        FROM articles a JOIN orgs o ON o.id = a.org_id
        WHERE (%s::text IS NULL OR a.subject = %s)
          AND (%s::text IS NULL OR %s = ANY(o.features))
          AND (NOT %s OR (o.in_default AND coalesce(a.language, 'English') = 'English'))
          {extra}
        ORDER BY coalesce(a.published_at, a.fetched_at) DESC, a.id DESC
        LIMIT %s
        """.format(extra=("AND " + filter_sql) if filter_sql else ""),
        [subject, subject, feature, feature, default_only, *filter_params, limit],
    )
    cols = ("id", "url", "title", "summary", "author", "published_at", "fetched_at",
            "image_file", "image_w", "image_h", "image_alt", "subject", "org_name", "slug", "org_url",
            "support_url", "support_label", "state", "city", "beat", "coverage",
            "coverage_type", "timezone", "model", "features", "org_feed",
            "in_default", "language")
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

    fonts_src = config.ASSETS_DIR / "fonts"
    if fonts_src.is_dir():
        fonts_dst = site / "fonts"
        fonts_dst.mkdir(parents=True, exist_ok=True)
        for font in fonts_src.glob("*.woff2"):
            shutil.copyfile(font, fonts_dst / font.name)
    masthead = config.ASSETS_DIR / "masthead.svg"
    if masthead.is_file():
        shutil.copyfile(masthead, site / "masthead.svg")

    with connect() as conn, conn.cursor() as cur:
        orgs = load_orgs(cur)
        articles = collapse_duplicates(
            load_articles(cur, FEED_PAGE_ARTICLES, default_only=True))
        all_articles = collapse_duplicates(
            load_articles(cur, FEED_PAGE_ARTICLES, apply_filters=False))

        # An image reused across many stories is the newsroom's house art or
        # a category placeholder, not this story's picture. Don't repeat it.
        cur.execute(
            "SELECT image_file FROM articles WHERE image_file IS NOT NULL "
            "GROUP BY image_file HAVING count(*) >= %s",
            (HOUSE_IMAGE_USES,),
        )
        house_images = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT count(*) FROM articles")
        n_articles = cur.fetchone()[0]
        n_states = len({o["state"] for o in orgs if o["state"]})
        intro = (f"<p><small>{len(orgs)} newsrooms · {n_states} states and D.C. · "
                 f"{n_articles} stories</small></p>")

        # Which newsrooms published in the last day — the map lights those red.
        cur.execute(
            "SELECT o.slug FROM articles a JOIN orgs o ON o.id = a.org_id "
            "WHERE coalesce(a.published_at, a.fetched_at) > now() - interval '24 hours' "
            "GROUP BY o.slug"
        )
        recent = {r[0] for r in cur.fetchall()}

        (site / "catalog.html").write_text(
            page(f"{config.SITE_NAME} — Catalog", render_catalog_index(orgs))
        )
        # One page per state, so the catalog stays small however big it gets.
        (site / "catalog").mkdir(parents=True, exist_ok=True)
        state_groups, _national = group_orgs_by_state(orgs)
        for state_name, group in state_groups.items():
            body = (f"<h1>{esc(state_name)}</h1>"
                    f"<p>{len(group)} newsrooms. "
                    f'<a href="../catalog.html">All states</a></p>'
                    + render_org_list(group, prefix="../"))
            slug = re.sub(r"[^a-z0-9]+", "-", state_name.lower()).strip("-")
            (site / "catalog" / f"{slug}.html").write_text(
                page(f"{config.SITE_NAME} — {state_name}", body, prefix="../")
            )
        # And one page per feature: Black-owned, Spanish, INN member...
        (site / "features").mkdir(parents=True, exist_ok=True)
        by_feature = collections.defaultdict(list)
        for org in orgs:
            for feature in (org.get("features") or []):
                by_feature[feature].append(org)
        for feature, group in by_feature.items():
            body = (f"<h1>{esc(feature)}</h1>"
                    f"<p>{len(group)} newsrooms. "
                    f'<a href="../catalog.html">Whole catalog</a></p>'
                    + render_org_list(sorted(group, key=lambda o: o["name"]), prefix="../"))
            slug = re.sub(r"[^a-z0-9]+", "-", feature.lower()).strip("-")
            (site / "features" / f"{slug}.html").write_text(
                page(f"{config.SITE_NAME} — {feature}", body, prefix="../")
            )
        (site / "resources.html").write_text(
            page(f"{config.SITE_NAME} — Resources", render_institutions(cur, orgs),
                 description="The funders, networks, associations and directories behind "
                             "the newsrooms in this catalog.")
        )
        (site / "map.html").write_text(
            page(f"{config.SITE_NAME} — Coverage map", render_map(orgs, recent=recent))
        )
        cur.execute(
            "SELECT subject, count(*) FROM articles WHERE subject IS NOT NULL "
            "GROUP BY subject ORDER BY subject"
        )
        subject_counts = cur.fetchall()

        def subject_nav(prefix="", current=None):
            links = [
                (f'{esc(name)} ({n})' if name == current
                 else f'<a href="{subject_href(name, prefix)}">{esc(name)}</a> ({n})')
                for name, n in subject_counts
            ]
            all_link = "All" if current is None else f'<a href="{prefix}index.html">All</a>'
            return f"<p>{all_link} · " + " · ".join(links) + "</p>"

        MENU_SUBJECTS[:] = [
            (name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
            for name, _n in subject_counts
        ]
        MENU_FEEDS[:] = [("feed.xml", "Everything")] + [
            (f"subjects/{slug}.xml", name) for name, slug in MENU_SUBJECTS
        ]

        # No headline, no counts, no subject bar: the front page opens on
        # the first story and everything else lives behind the menu.
        n_feed_pages = write_feed_pages(
            site, cur, articles, "feed", config.SITE_NAME, "Feed",
            skip_images=house_images, first_name="index.html",
            show_heading=False, intro=search_form(),
        )

        # Everything, including the ordinary commercial weeklies the default
        # view leaves out.
        write_feed_pages(
            site, cur, all_articles, "everything",
            f"{config.SITE_NAME} — Everything", "Every newsroom",
            skip_images=house_images, with_related=False,
            intro='<p class="meta">Every newsroom in the catalog, including the '
                  'commercial weeklies the front page leaves out. '
                  '<a href="index.html">Back to the default feed</a>.</p>',
        )

        # One feed per tag, so a lozenge is a real destination.
        (site / "tags").mkdir(parents=True, exist_ok=True)
        cur.execute(
            "SELECT unnest(features) AS f, count(*) FROM orgs GROUP BY 1 ORDER BY 2 DESC"
        )
        tag_counts = dict(cur.fetchall())
        for _group, group_tags in TAG_GROUPS:
            for tag in group_tags:
                tag_articles = collapse_duplicates(
                    load_articles(cur, FEED_PAGE_ARTICLES, feature=tag)
                )
                if not tag_articles:
                    continue
                n_rooms = tag_counts.get(tag, 0)
                write_feed_pages(
                    site, cur, tag_articles, tag_slug(tag),
                    f"{config.SITE_NAME} — {tag}", tag, prefix="../",
                    skip_images=house_images, subdir="tags", with_related=False,
                    feed_href=f"tags/{tag_slug(tag)}.xml",
                    feed_title=f"{config.SITE_NAME} — {tag}",
                    intro=f'<p class="meta">{n_rooms} newsrooms tagged {esc(tag)}. '
                          f'<a href="../catalog.html">All tags</a></p>',
                )

        for name, _n in subject_counts:
            subject_articles = collapse_duplicates(
                load_articles(cur, FEED_PAGE_ARTICLES, subject=name)
            )
            write_feed_pages(
                site, cur, subject_articles, name.lower().replace(" ", "-"),
                f"{config.SITE_NAME} — {name}", name, prefix="../",
                subject_nav=subject_nav(prefix="../", current=name),
                skip_images=house_images, subdir="subjects",
                feed_href=f"subjects/{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.xml",
                feed_title=f"{config.SITE_NAME} — {name}",
                # One pgvector lookup per item is affordable on the main feed;
                # repeating it for every subject page is what made the build
                # take a quarter of an hour.
                with_related=False,
            )
        (site / "big-stories.html").write_text(page(
            f"{config.SITE_NAME} — Big stories", render_big_stories(cur),
            description="Stories running in several newsrooms at once."))
        (site / "story-links.html").write_text(page(
            f"{config.SITE_NAME} — Story links", render_story_links(cur),
            description="Separate newsrooms reporting the same pressure."))
        (site / "about.html").write_text(page(
            f"{config.SITE_NAME} — About", render_about(cur, orgs),
            description="What this is, where the data comes from, and who to credit."))

        for org in orgs:
            body = render_org_page(cur, org)
            (site / "orgs" / f"{org['slug']}.html").write_text(
                page(f"{config.SITE_NAME} — {org['name']}", body, prefix="../")
            )

        onepage_articles = articles[:ONEPAGE_ARTICLES]
        onepage = "\n<hr>\n".join(
            [
                f"<h1>{esc(config.SITE_NAME)}</h1>\n{intro}",
                '<div id="catalog">' + render_catalog_index(orgs) + "</div>",
                '<div id="map">' + render_map(orgs, mode="onepage", recent=recent) + "</div>",
                '<div id="feed">' + render_feed(cur, onepage_articles, mode="onepage",
                                                 skip_images=house_images,
                                                 with_related=False) + "</div>",
                '<div id="connections">' + render_big_stories(cur, mode="onepage") + "</div>",
            ]
        )
        onepage_nav = (
            f'<a href="#feed">Feed</a>\n<a href="#catalog">Catalog</a>\n'
            f'<a href="#map">Map</a>\n<a href="#connections">Big stories</a>'
        )
        (site / "onepage.html").write_text(page(config.SITE_NAME, onepage, nav_html=onepage_nav))

        # --- RSS, one per subject plus the whole feed --------------------
        site_url = config.SITE_URL.rstrip("/")
        (site / "feed.xml").write_text(syndicate.render_rss(
            articles, config.SITE_NAME,
            "Local newsrooms built to last, newest first. Every story links back "
            "to the newsroom that reported it.",
            "feed.xml", site_url))
        for name, slug in MENU_SUBJECTS:
            subject_articles = load_articles(cur, syndicate.RSS_ITEMS, subject=name)
            (site / "subjects" / f"{slug}.xml").write_text(syndicate.render_rss(
                subject_articles, f"{config.SITE_NAME} — {name}",
                f"{name} reporting from local newsrooms across the United States.",
                f"subjects/{slug}.xml", site_url))

        # One RSS per tag as well as per subject.
        emitted_tags = []
        for _group, group_tags in TAG_GROUPS:
            for tag in group_tags:
                tag_articles = load_articles(cur, syndicate.RSS_ITEMS, feature=tag)
                if not tag_articles:
                    continue
                emitted_tags.append(tag)
                (site / "tags" / f"{tag_slug(tag)}.xml").write_text(syndicate.render_rss(
                    tag_articles, f"{config.SITE_NAME} — {tag}",
                    f"Stories from newsrooms tagged {tag}.",
                    f"tags/{tag_slug(tag)}.xml", site_url))

        (site / "feeds.html").write_text(page(
            f"{config.SITE_NAME} — RSS feeds",
            render_feeds_page(subject_counts, emitted_tags),
            description="Every RSS feed this site publishes, by subject and by tag."))

        # --- the small files a site is expected to have ------------------
        drawn_icon = config.ASSETS_DIR / "favicon.svg"
        (site / "favicon.svg").write_text(
            drawn_icon.read_text() if drawn_icon.is_file() else syndicate.FAVICON)
        (site / "404.html").write_text(page(
            f"{config.SITE_NAME} — not found",
            "<h1>Not here</h1>"
            "<p>That page has moved or never existed. The menu above has "
            'everything, or start from <a href="/">the feed</a>.</p>'))
        (site / "robots.txt").write_text(syndicate.ROBOTS.format(site_url=site_url))
        sitemap_paths = ["", "catalog.html", "map.html", "resources.html",
                 "big-stories.html", "story-links.html", "about.html", "feeds.html"]
        sitemap_paths += [f"catalog/{re.sub(r'[^a-z0-9]+', '-', n.lower()).strip('-')}.html"
                          for n in state_groups]
        sitemap_paths += [f"features/{re.sub(r'[^a-z0-9]+', '-', f.lower()).strip('-')}.html"
                          for f in by_feature]
        sitemap_paths += [f"orgs/{o['slug']}.html" for o in orgs]
        (site / "sitemap.xml").write_text(syndicate.render_sitemap(sitemap_paths, site_url))

        # --- the plain-text edition --------------------------------------
        write_text_edition(site, cur, articles, orgs)

        path = export_catalog_json(orgs)
        n_img = sum(1 for a in articles if a.get("image_file"))
        n_folded = sum(len(a.get("_also", [])) for a in articles)
        print(f"built site/ ({len(orgs)} orgs, {len(articles)} feed items over {n_feed_pages} pages, "
              f"{n_img} with images, {n_folded} reprints folded in, "
              f"{len(subject_counts)} subjects) and {path.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
