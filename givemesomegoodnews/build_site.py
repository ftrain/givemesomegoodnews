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
    ("index.html", "Feed"),
    ("catalog.html", "Catalog"),
    ("map.html", "Map"),
    ("connections.html", "Connections"),
    ("/search", "Search"),
    ("onepage.html", "Everything on one page"),
]


def stylesheet(prefix=""):
    """One inline stylesheet, so every page stays a single self-contained file.

    IBM Plex is served from this site, not a font CDN — same reasoning as the
    image cache: no third party needs to see who is reading.
    """
    return f"""<style>
@font-face {{
  font-family: 'IBM Plex Sans';
  src: url({prefix}fonts/ibm-plex-sans.woff2) format('woff2');
  font-weight: 100 700; font-style: normal; font-display: swap;
}}
@font-face {{
  font-family: 'IBM Plex Mono';
  src: url({prefix}fonts/ibm-plex-mono.woff2) format('woff2');
  font-weight: 400; font-style: normal; font-display: swap;
}}
:root {{
  --fg: #1a1a1a; --bg: #fff; --muted: #5c5c5c;
  --rule: #d8d8d8; --link: #c8102e; --visited: #8c0b20;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --fg: #e9e9e9; --bg: #121212; --muted: #a2a2a2;
    --rule: #343434; --link: #ff6b6b; --visited: #cf8f8f;
  }}
}}
html {{ -webkit-text-size-adjust: 100%; }}
body {{
  font-family: 'IBM Plex Sans', system-ui, -apple-system, sans-serif;
  font-size: 1.125rem; line-height: 1.55;
  color: var(--fg); background: var(--bg);
  max-width: 40rem; margin: 0 auto; padding: 1rem 1rem 4rem;
  overflow-wrap: break-word;
}}
/* No underline at rest; colour carries the link, underline on hover so
   there is still a non-colour cue when you reach for one. */
a {{ color: var(--link); text-decoration: none; }}
a:visited {{ color: var(--visited); }}
a:hover, a:focus {{ text-decoration: underline; }}
h1 {{ font-size: 1.8rem; line-height: 1.2; font-weight: 700; margin: 1.5rem 0 1rem; }}
h2 {{ font-size: 1.25rem; font-weight: 600; margin: 2.5rem 0 0.5rem; }}
p {{ margin: 0 0 0.75rem; }}
ul {{ padding-left: 1.25rem; }}
li {{ margin-bottom: 0.5rem; }}
small, small a {{ font-family: 'IBM Plex Mono', ui-monospace, monospace; font-size: 0.8rem; }}
small {{ color: var(--muted); }}
/* One post per block: breathing room, then a rule to the next. */
article {{
  padding: 2rem 0;
  border-bottom: 1px solid var(--rule);
}}
article p:last-child {{ margin-bottom: 0; }}
/* Contain the floated image so it can't spill into the next post. */
article::after {{ content: ""; display: block; clear: both; }}
article h2 {{ font-size: 1.25rem; line-height: 1.25; margin: 0.2rem 0 0.4rem; }}
a.shot {{ float: left; width: 50%; margin: 0.3rem 1rem 0.4rem 0; }}
a.shot img {{ width: 100%; height: auto; }}
p.more {{ margin-top: 0.75rem; }}
p.footer-line {{ clear: both; padding-top: 0.75rem; }}
.yours {{ color: var(--muted); }}
/* A 50% float on a phone leaves ~170px of text per line; stack instead. */
@media (max-width: 34rem) {{
  a.shot {{ float: none; width: 100%; margin: 0 0 0.75rem; }}
}}
img {{ max-width: 100%; height: auto; display: block; }}
svg {{ max-width: 100%; height: auto; }}
blockquote {{ margin: 0 0 1rem; padding-left: 1rem; border-left: 3px solid var(--rule); }}
hr {{ border: 0; border-top: 1px solid var(--rule); margin: 2.5rem 0; }}
details.menu {{ margin: 0 0 0.5rem; }}
details.menu > summary {{
  list-style: none; cursor: pointer; padding: 0.4rem 0;
  font-weight: 600; font-size: 1.05rem;
}}
details.menu > summary::-webkit-details-marker {{ display: none; }}
details.menu .bars {{ color: var(--muted); margin-right: 0.35rem; }}
details.menu .wordmark a {{ color: var(--fg); text-decoration: none; }}
details.menu nav {{
  display: flex; flex-direction: column; gap: 0.5rem;
  padding: 0.6rem 0 0.4rem 1.6rem; border-top: 1px solid var(--rule); margin-top: 0.4rem;
}}
input, button {{
  font: inherit; font-size: 1rem; padding: 0.4rem 0.6rem;
  border: 1px solid var(--rule); border-radius: 3px;
  background: var(--bg); color: var(--fg);
}}
input[type=search] {{ width: min(22rem, 70%); }}
button {{ cursor: pointer; color: var(--link); }}
</style>"""


def page(title, body, prefix="", nav_html=None, scripts=""):
    links = nav_html or "\n".join(
        f'<a href="{href if href.startswith("/") else prefix + href}">{esc(label)}</a>'
        for href, label in NAV
    )
    # <details> gives a hamburger that works with JavaScript switched off.
    home = prefix + "index.html"
    nav = (
        f'<details class="menu"><summary><span class="bars" aria-hidden="true">\u2630</span> '
        f'<span class="wordmark"><a href="{home}">{esc(config.SITE_NAME)}</a></span></summary>'
        f'<nav>{links}</nav></details>'
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
{stylesheet(prefix)}
</head>
<body>
{nav}
<hr>
{body}
<hr>
<p><small>Generated {generated}. About text quoted from each newsroom's own
About page; headlines, summaries and images from their public feeds, linking to
the original. <a href="{config.REPO_URL}">{config.REPO_LABEL}</a></small></p>
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
        '<button type="submit">Search</button></p></form>'
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


LOCAL_TIME_SCRIPT = """<script>
/* Each dateline is the newsroom's own local time. Where the reader sits in
   a different zone, append theirs. Nothing here is required to read the
   page — with JS off you still get the publication's time. */
window.localizeTimes = function (root) {
  var nodes = (root || document).querySelectorAll("time[data-pub]:not([data-done])");
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    el.setAttribute("data-done", "1");
    var when = new Date(el.getAttribute("datetime"));
    if (isNaN(when.getTime())) continue;
    try {
      var mine = new Intl.DateTimeFormat(undefined, {
        hour: "numeric", minute: "2-digit", timeZoneName: "short"
      }).format(when);
      if (!mine || mine === el.getAttribute("data-pub")) continue;
      var span = document.createElement("span");
      span.className = "yours";
      span.textContent = " \u00b7 " + mine + " your time";
      el.parentNode.insertBefore(span, el.nextSibling);
    } catch (e) { /* no Intl: the publication's time stands on its own */ }
  }
};
window.localizeTimes(document);
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
        if (window.localizeTimes) window.localizeTimes(items);
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
                     first_name=None, intro=""):
    """Split a feed into pages so no single page carries the whole crawl."""
    target = (site / subdir) if subdir else site
    target.mkdir(parents=True, exist_ok=True)
    chunks = [articles[i:i + FEED_PAGE_SIZE] for i in range(0, len(articles), FEED_PAGE_SIZE)] or [[]]
    for index, chunk in enumerate(chunks):
        nav = subject_nav if index == 0 else None
        body = render_feed(cur, chunk, prefix=prefix, heading=heading,
                           subject_nav=nav, skip_images=skip_images,
                           page_index=index, page_count=len(chunks), stem=stem,
                           intro=intro)
        head = title if index == 0 else f"{title} — page {index + 1}"
        name = first_name if (index == 0 and first_name) else feed_page_name(stem, index)
        target.joinpath(name).write_text(
            page(head, body, prefix=prefix, scripts=FEED_SCRIPT)
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
    return f'<a href="{esc(url)}"><strong>{esc(label)}</strong></a>'


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
    org_page = a["org_url"] if mode == "onepage" else f"{prefix}orgs/{a['slug']}.html"
    pub = f'<a href="{esc(org_page)}">{esc(a["org_name"])}</a>'
    return " / ".join(esc(part) for part in (first, middle) if part) + " / " + pub


def render_feed_item(cur, a, mode="site", prefix="", with_related=True, skip_images=()):
    moment = a["published_at"] or a["fetched_at"]
    dateline = local_dateline(moment, a.get("state"), a.get("timezone"))
    pub_time = local_time(moment, a.get("state"), a.get("timezone"))
    stamp = []
    if dateline:
        stamp.append(
            f'<time datetime="{moment.astimezone(timezone.utc).isoformat()}" '
            f'data-pub="{esc(pub_time)}">{esc(dateline)}</time>'
        )
    if a.get("subject"):
        label = esc(a["subject"])
        stamp.append(label if mode == "onepage" else
                     f'<a href="{subject_href(a["subject"], prefix)}">{label}</a>')

    out = ["<article>"]
    if stamp:
        out.append(f"<p><small>{' \u00b7 '.join(stamp)}</small></p>")
    out.append(f"<p><small>{place_line(a, mode, prefix)}</small></p>")
    out.append(f'<h2><a href="{esc(a["url"])}">{esc(a["title"])}</a></h2>')
    if a.get("author"):
        out.append(f'<p><small>By {esc(a["author"])}</small></p>')

    if a.get("image_file") and a["image_file"] not in skip_images:
        size = ""
        if a.get("image_w") and a.get("image_h"):
            size = f' width="{a["image_w"]}" height="{a["image_h"]}"'
        out.append(
            f'<a class="shot" href="{esc(a["url"])}">'
            f'<img src="{prefix}img/{esc(a["image_file"])}" alt=""{size} '
            f'loading="lazy" decoding="async"></a>'
        )
    if a.get("summary"):
        out.append(f"<p>{esc(a['summary'][:400])}</p>")

    out.append(
        f'<p class="more"><a href="{esc(a["url"])}">Read more '
        f'<span aria-hidden="true">\u2192</span></a></p>'
    )

    if a.get("_also"):
        others = " \u00b7 ".join(
            f'<a href="{esc(d["url"])}">{esc(d["org_name"])}</a>' for d in a["_also"]
        )
        out.append(f"<p><small>Also in {others}</small></p>")

    if with_related:
        echoes = []
        for r_title, r_url, r_org, r_slug, r_org_url, r_sim in related_to(cur, a["id"]):
            if classify_pair(r_sim, a["title"], r_title) == "kindred" and len(echoes) < 2:
                echoes.append(f'<a href="{esc(r_url)}">{esc(r_org)}: {esc(r_title)}</a>')
        if echoes:
            out.append(f"<p><small>Echo: {' \u00b7 '.join(echoes)}</small></p>")

    # The newsroom in its own words — one sentence chosen from their About
    # page — then the ask.
    about = a.get("tagline") or ""
    org_page = a["org_url"] if mode == "onepage" else f"{prefix}orgs/{a['slug']}.html"
    tail = []
    if about:
        tail.append(f'<a href="{esc(org_page)}">{esc(about)}</a>')
    tail.append(support_link(a))
    out.append(f'<p class="footer-line"><small>{" | ".join(tail)}</small></p>')
    out.append("</article>")
    return "\n".join(out)


def render_feed(cur, articles, mode="site", prefix="", with_related=True, heading="Feed",
                subject_nav=None, skip_images=(), page_index=0, page_count=1, stem=None,
                intro=""):
    parts = []
    if page_index == 0:
        parts.append(f"<h1>{esc(heading)}</h1>")
        if intro:
            parts.append(intro)
        parts.append(search_form())
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
        "<p>Nearest neighbours across state lines, by cosine distance.</p>",
        "<h2>Same story, several outlets</h2>",
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
        "<p>Distinct stories — separate newsrooms, separate "
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


def render_org_page(cur, org):
    parts = [f'<h1><a href="{esc(org["url"])}">{esc(org["name"])}</a></h1>', f"<p>{meta_line(org)}</p>"]
    if org.get("support_url"):
        label = org.get("support_label") or "Support"
        parts.append(f'<p><a href="{esc(org["support_url"])}"><strong>{esc(label)}</strong></a></p>')
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
    "support_url", "support_label",
    "about_text", "about_source_url", "about_fetched_at",
)


def load_orgs(cur):
    cur.execute(f"SELECT {', '.join(ORG_COLUMNS)} FROM orgs ORDER BY name")
    return [dict(zip(ORG_COLUMNS, row)) for row in cur.fetchall()]


def load_articles(cur, limit, subject=None):
    cur.execute(
        """
        SELECT a.id, a.url, a.title, a.summary, a.author, a.published_at, a.fetched_at,
               a.image_file, a.image_w, a.image_h, a.subject,
               o.name AS org_name, o.slug, o.url AS org_url,
               o.support_url, o.support_label,
               o.state, o.city, o.beat, o.coverage, o.coverage_type,
               o.timezone, o.tagline
        FROM articles a JOIN orgs o ON o.id = a.org_id
        WHERE (%s::text IS NULL OR a.subject = %s)
        ORDER BY coalesce(a.published_at, a.fetched_at) DESC, a.id DESC
        LIMIT %s
        """,
        (subject, subject, limit),
    )
    cols = ("id", "url", "title", "summary", "author", "published_at", "fetched_at",
            "image_file", "image_w", "image_h", "subject", "org_name", "slug", "org_url",
            "support_url", "support_label", "state", "city", "beat", "coverage",
            "coverage_type", "timezone", "tagline")
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

    with connect() as conn, conn.cursor() as cur:
        orgs = load_orgs(cur)
        articles = collapse_duplicates(load_articles(cur, FEED_PAGE_ARTICLES))

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

        (site / "catalog.html").write_text(page(f"{config.SITE_NAME} — Catalog", render_catalog(orgs)))
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

        n_feed_pages = write_feed_pages(
            site, cur, articles, "feed", config.SITE_NAME, "Feed",
            subject_nav=subject_nav(), skip_images=house_images,
            first_name="index.html", intro=intro,
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
            )
        (site / "connections.html").write_text(page(f"{config.SITE_NAME} — Connections", render_connections(cur)))

        for org in orgs:
            body = render_org_page(cur, org)
            (site / "orgs" / f"{org['slug']}.html").write_text(
                page(f"{config.SITE_NAME} — {org['name']}", body, prefix="../")
            )

        onepage_articles = articles[:ONEPAGE_ARTICLES]
        onepage = "\n<hr>\n".join(
            [
                f"<h1>{esc(config.SITE_NAME)}</h1>\n{intro}",
                '<div id="catalog">' + render_catalog(orgs, mode="onepage") + "</div>",
                '<div id="map">' + render_map(orgs, mode="onepage", recent=recent) + "</div>",
                '<div id="feed">' + render_feed(cur, onepage_articles, mode="onepage",
                                                 skip_images=house_images) + "</div>",
                '<div id="connections">' + render_connections(cur, mode="onepage") + "</div>",
            ]
        )
        onepage_nav = (
            f'<a href="#feed">Feed</a>\n<a href="#catalog">Catalog</a>\n'
            f'<a href="#map">Map</a>\n<a href="#connections">Connections</a>'
        )
        (site / "onepage.html").write_text(page(config.SITE_NAME, onepage, nav_html=onepage_nav))

        path = export_catalog_json(orgs)
        n_img = sum(1 for a in articles if a.get("image_file"))
        n_folded = sum(len(a.get("_also", [])) for a in articles)
        print(f"built site/ ({len(orgs)} orgs, {len(articles)} feed items over {n_feed_pages} pages, "
              f"{n_img} with images, {n_folded} reprints folded in, "
              f"{len(subject_counts)} subjects) and {path.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
