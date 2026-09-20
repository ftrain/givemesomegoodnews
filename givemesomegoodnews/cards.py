"""One story, as a card, and everything in the rail beside it.

A card is where the site does most of its work: whose state this is, who
published it, when it ran there, the headline, the byline, the picture, and
the ask. The rail is a column of small answers — a flag, a locator, what kind
of newsroom this is, how often it publishes — and two of them open in place,
so a reader can ask who a newsroom is or who a reporter is without leaving
the feed.
"""

import math
import re
from datetime import timezone
from html import escape as esc
from urllib.parse import quote, urlencode

from PIL import Image

from . import config, reporters
from .albers import state_locator
from .dedupe import classify_pair
from .links import (STATE_NAMES, feature_href, feed_page_name, image_href,
                    place_label, subject_href)
from .prose import (about_opening, clip_summary, credit, newsroom_phrase,
                    tighten)
from .tags import TAG_PRIORITY, tag_slug
from .timezones import local_dateline, local_time
from .topics import card_topics


# An image on this many articles is house art, not story art.
HOUSE_IMAGE_USES = 4


# Pictures near the top of a page load straight away rather than on scroll.
EAGER_IMAGES = 3


# How often each newsroom publishes, likewise filled in by main() from one
# grouped query. Keyed by org id; see cadence_by_org().
CADENCE = {}


# Headlines a reporter's disclosure lists before it stops.
REPORTER_HEADLINES = 3


# Every byline the site resolves to a person, keyed by reporter identity and
# filled in by main() from one query before anything renders. A card then
# costs a dictionary lookup rather than a query of its own.
REPORTERS = {}


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


def related_to(cur, article_id, limit=4):
    """The same story somewhere else: nearest neighbours in another state.

    What makes this affordable is the HNSW index on articles.embedding, and
    an index can only be walked from a vector it has been handed. This used
    to order by `a.embedding <=> b.embedding` with `a` fetched in the same
    statement, and a column from a join is not a constant, so Postgres
    could not use the index at all: every card on the front page cost a
    scan of all seventy-nine thousand embeddings — four hundred
    milliseconds each, and most of the build. Handing the vector over as a
    scalar subquery makes it the constant the walk starts from, and the
    same question is answered in two.

    Both "somewhere else" conditions ride inside that scan rather than
    around it, so the walk stops as soon as enough rows have passed them
    rather than gathering neighbours to throw away. The answer is the
    index's, so it is the approximate one: near neighbours, not provably
    the nearest, which is all a row of "also reported in" ever needed.
    """
    cur.execute(
        """
        SELECT b.title, b.url, o2.name, o2.slug, o2.url AS org_url,
               1 - (b.embedding <=> (SELECT embedding FROM articles WHERE id = %(id)s))
                   AS sim
        FROM articles b JOIN orgs o2 ON o2.id = b.org_id
        WHERE b.embedding IS NOT NULL
          AND b.org_id <> (SELECT org_id FROM articles WHERE id = %(id)s)
          AND o2.state IS DISTINCT FROM (SELECT o.state FROM articles a
                                           JOIN orgs o ON o.id = a.org_id
                                          WHERE a.id = %(id)s)
        ORDER BY b.embedding <=> (SELECT embedding FROM articles WHERE id = %(id)s)
        LIMIT %(limit)s
        """,
        {"id": article_id, "limit": limit},
    )
    return [r for r in cur.fetchall() if r[5] >= 0.28]


def support_link(article):
    """The ask, for the newsrooms that have somewhere to send it.

    A front page is not a donate page. Labelling one "Support" makes a
    promise the link cannot keep, and a reader who follows it lands on a
    masthead with no idea what they were meant to do there. Where
    fetch_support found nothing, the rail says nothing.
    """
    if not article.get("support_url"):
        return ""
    label = article.get("support_label") or "Donate"
    return (f'<a class="lozenge give" href="{esc(article["support_url"])}">'
            f'{esc(label)}</a>')


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


def disclosure(marker, panel_html, extra_class=""):
    """A marker that opens a panel in place, with no script behind it.

    The marker is whatever inline HTML belongs next to the thing being
    disclosed; the caret is added here so every disclosure on the page opens
    the same way. Nothing to disclose means no marker at all, rather than a
    marker that opens onto an empty panel.

    Everything about the behaviour is the browser's: the summary takes Tab,
    toggles on Enter and Space, announces itself as expanded or collapsed,
    and holds that state for as long as the page lives. So nothing here adds
    a role, an `aria-expanded`, an `open` attribute or a line of script —
    each of those would only compete with what the element already does.
    """
    if not panel_html:
        return ""
    classes = f"disc {extra_class}".strip()
    return (f'<details class="{classes}">'
            f'<summary><span class="disc-line">{marker}'
            f'<span class="disc-cue">Profile</span></span></summary>'
            f'<div class="disc-panel">{panel_html}</div></details>')


def org_profile_panel(a, mode="site", prefix=""):
    """The newsroom in its own words, what it covers, what it is, where next."""
    rows = []
    quote = about_opening(a.get("about_text"))
    if quote:
        rows.append(f"<blockquote><p>{esc(quote)}</p></blockquote>")
        rows.append('<p class="meta">— in their own words, from their About page.</p>')
    if a.get("coverage"):
        rows.append(f'<p>Covers {esc(a["coverage"])}.</p>')
    # The panel opens a finger's width from the rail, and a lozenge repeated
    # there reads as a second, different one: a card whose newsroom is
    # tagged Nonprofit and takes donations was showing Nonprofit and Donate
    # twice over, side by side. So the panel carries only what the rail had
    # no room for — the tags past its cap — and leaves the ask to the rail,
    # which is always showing it when this panel is open.
    tags = tag_links(a, prefix if mode != "onepage" else "", after=RAIL_TAG_CAP)
    if tags:
        rows.append(f"<p>{tags}</p>")
    links = [f'<a class="lozenge" href="{esc(a["org_url"])}">Their site</a>']
    if mode != "onepage":
        links.append(f'<a class="lozenge" href="{prefix}orgs/{esc(a["slug"])}.html">'
                     f"Newsroom page</a>")
    rows.append(f'<p>{"".join(links)}</p>')
    return "\n".join(rows)


def reporter_span(first_at, last_at):
    """The period a reporter's work here covers, to the month."""
    if not first_at or not last_at:
        return ""
    first, last = first_at.strftime("%B %Y"), last_at.strftime("%B %Y")
    if first == last:
        return f"All of it from {last}."
    return f"Their work here runs from {first} to {last}."


def reporter_of(a):
    """The person behind an item's byline, or None where it names nobody.

    `REPORTERS` is assembled once per build, so this is a lookup rather than
    a query, and a byline that resolves to nobody is simply not in there.
    """
    return REPORTERS.get(reporters.reporter_key(a.get("author")))


def reporter_facts(who):
    """The reporter in sentences, for anywhere that can carry sentences."""
    said = [reporters.prolificacy(who["n_stories"]),
            newsroom_phrase(who["newsrooms"]),
            reporter_span(who["first_at"], who["last_at"])]
    return " ".join(part for part in said if part)


def reporter_panel(a):
    """What this site holds under one byline: how much, where, when, what.

    Everything the panel points at is a story on the newsroom that published
    it. There is no page of the reporter's own to send anyone to yet — the
    build writes `orgs/` and nothing else — so the panel ends at the
    headlines rather than at a link to a file that is never written.
    """
    who = reporter_of(a)
    if not who:
        return ""
    rows = [f"<p>{esc(reporter_facts(who))}</p>"]
    if who["recent"]:
        items = "".join(
            f'<li><a href="{esc(r["url"])}">{esc(tighten(r["title"]))}</a></li>'
            for r in who["recent"]
        )
        rows.append(f"<p>Most recently:</p><ul>{items}</ul>")
    return "".join(rows)


# The locator is a thumbnail, not a map: one state's outline about this
# many pixels across, with a single mark on it.
LOCATOR_SIZE = 64


# STATE_NAMES stops at the fifty states and the district; a newsroom in a
# territory still has to be named in words.
REGION_NAMES = {
    **STATE_NAMES, "PR": "Puerto Rico", "GU": "Guam", "AS": "American Samoa",
    "VI": "U.S. Virgin Islands", "MP": "Northern Mariana Islands",
}


# The GeoJSON spells the district out; the rest of the site says it short.
GEOJSON_STATE_NAMES = {"DC": "District of Columbia"}


# Outlets organised around a region rather than a city: naming a city they
# do not cover would be a wrong answer, so they name their coverage.
WIDE_COVERAGE = ("state", "regional", "network", "national")


# How wide the flag renders in the rail. Big enough now that a seal is a
# device rather than a smudge, though the two-letter code stays beside it:
# a flag is recognised, a seal is not, and the code is the part that is
# text. The files in assets/flags are 96px wide, so this is exactly 2x on a
# dense screen — the ceiling, and the reason not to go wider without
# re-rendering the assets.
FLAG_WIDTH = 48


# Flags are not all one shape — Ohio is a pennant, Rhode Island is nearly
# square — so the height is read off each file the first time it is asked
# for, and cached for the rest of the build. An <img> given the wrong
# proportions reserves the wrong box and jumps when the file lands.
_FLAG_BOX = {}


def flag_box(code):
    """The width and height to draw a state's flag at, or None if we have no
    flag for it."""
    if code not in _FLAG_BOX:
        path = config.ASSETS_DIR / "flags" / f"{code.lower()}.webp"
        box = None
        if path.is_file():
            with Image.open(path) as flag:
                width, height = flag.size
            box = (FLAG_WIDTH, max(1, round(FLAG_WIDTH * height / width)))
        _FLAG_BOX[code] = box
    return _FLAG_BOX[code]


def state_identity(a, prefix=""):
    """Which state's newsroom this is, as its flag.

    A flag is recognised before it is read, which is the whole job at the
    top of the rail, and at 48px it carries that on its own. The state is
    still named in text for anyone not seeing the picture — the flag's alt
    is the state's full name, and the region line below the locator says
    where in it — so nothing is lost with images off.

    An outlet with no state, or one whose beat is the country, has no state
    to fly, and a code with no flag on disk has none either. Both get
    nothing rather than a marker reading National above a region line that
    is about to say National again: one statement of where, not two.
    """
    code = (a.get("state") or "").upper()
    if not code or (a.get("coverage_type") or "") == "national":
        return ""
    name = REGION_NAMES.get(code)
    box = flag_box(code) if name else None
    img = ""
    if box:
        # Not lazy-loaded, unlike the story photos: a flag is two kilobytes,
        # every card in the feed carries one, and deferring it leaves an
        # empty box where the answer to "whose newsroom is this" should be.
        img = (f'<img class="flag" src="{prefix}flags/{code.lower()}.webp" '
               f'width="{box[0]}" height="{box[1]}" alt="{esc(name)}">')
    return f'<p class="ident">{img}</p>' if img else ""


def locator_state_name(a):
    """The state whose outline belongs beside this story, if any."""
    code = (a.get("state") or "").upper()
    if not code or (a.get("coverage_type") or "") == "national":
        return None
    return GEOJSON_STATE_NAMES.get(code) or REGION_NAMES.get(code)


def locator_map(a):
    """A small outline of the newsroom's state with its place marked on it.

    The geocode's precision decides the mark. A place-level coordinate is a
    dot; a county one knows an area and not a point, so it shades one rather
    than claim a precision the gazetteer never had, and a state-level one
    shades the whole state. So does a statewide outlet, because that is what
    it covers. Coordinates hand-set in the YAML carry no precision at all;
    somebody chose them for that newsroom, so they are treated as a place.

    Decorative: the region line underneath carries the same information in
    words, so this is hidden from screen readers rather than described.
    """
    name = locator_state_name(a)
    if not name:
        return ""
    fit = state_locator(config.STATES_GEOJSON, name, LOCATOR_SIZE)
    if fit is None:
        # A territory with no outline in the GeoJSON. The region line names
        # it; an empty box would say less than nothing.
        return ""

    precision = (a.get("geo_precision") or "").lower()
    lat, lon = a.get("lat"), a.get("lon")
    mark = ""
    if ((a.get("coverage_type") or "") != "state" and precision != "state"
            and lat is not None and lon is not None):
        x, y = fit.point(float(lon), float(lat))
        # A coordinate outside its own state is a bad geocode; shade the
        # state rather than put a mark somewhere the reader cannot see.
        if fit.contains(x, y):
            mark = (f'<circle class="near" cx="{x}" cy="{y}" r="7"/>' if precision == "county"
                    else f'<circle class="here" cx="{x}" cy="{y}" r="2.4"/>')
    return (f'<svg class="locator" viewBox="0 0 {fit.width} {fit.height}" '
            f'width="{fit.width}" height="{fit.height}" aria-hidden="true" '
            f'focusable="false"><path class="{"state" if mark else "state whole"}" '
            f'd="{fit.path}"/>{mark}</svg>')


def region_parts(a):
    """The area the locator shades, as the pieces it is named in.

    Each piece is its words and the search that offers more from there, so
    the line under the map is both the answer to "where is this newsroom"
    and the way to the rest of that place. A piece with nowhere to send
    anyone is still named; it just isn't a link.
    """
    code = (a.get("state") or "").upper()
    state_name = REGION_NAMES.get(code)
    state_href = f"/search?state={quote(code)}" if state_name else None
    coverage = a.get("coverage")
    coverage_type = a.get("coverage_type") or ""
    if coverage_type in WIDE_COVERAGE:
        if coverage_type == "national":
            return [(coverage or "National", "/search?national=1")]
        return [(coverage or state_name or "National", state_href)]
    city = a.get("city")
    precision = (a.get("geo_precision") or "").lower()
    if not city or precision == "state":
        text = coverage or state_name
        return [(text, state_href)] if text else []
    if state_name and state_name.startswith(city):
        return [(state_name, state_href)]  # the district: city and state are one name
    # A county-level geocode places the newsroom near its city, not in it.
    where = city if precision != "county" or city.endswith("County") else f"{city} area"
    # The search is for the city itself either way; "area" is a hedge about
    # where the newsroom sits, not a different place to look in.
    city_href = "/search?" + urlencode(
        {"place": city, **({"state": code} if code else {})})
    parts = [(where, city_href)]
    if state_name:
        parts.append((state_name, state_href))
    return parts


def region_name(a):
    """The area the locator shades, in words, so it survives images off."""
    return ", ".join(text for text, _ in region_parts(a))


def region_line(a):
    """Where the newsroom is, once, under the map that draws the same thing.

    The card used to open with this as a row of lozenges as well — the state
    and the city as tags above the headline, the flag, the map, and then
    these words: a card saying where it was four times over. The words stay,
    because they are what survives images off and what names the shape above
    them, and they take the lozenges' links with them.
    """
    parts = region_parts(a)
    if not parts:
        return ""
    line = ", ".join(
        f'<a href="{esc(href)}">{esc(text)}</a>' if href else esc(text)
        for text, href in parts)
    return f'<p class="region">{line}</p>'


# Four weeks. Long enough that a weekly paper reads as a rate rather than
# noise, short enough that a newsroom which has gone quiet stops being
# described as though it hadn't.
CADENCE_WINDOW_DAYS = 28


# Small numbers belong in the sentence, not standing on their own; past nine
# the numeral is what a reader actually reads.
COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
               7: "seven", 8: "eight", 9: "nine"}


def cadence_by_org(cur):
    """How much each newsroom published lately, in one pass over the archive.

    A feed page is thirty cards and the answer is the same all build, so this
    is a grouped query run once from main() and handed down as a mapping, not
    a lookup per card.

    Two numbers per org, because the count alone would lie. How many stories
    landed inside the averaging window, and whether the collected history
    reaches back past the start of that window at all — an org first crawled
    on Tuesday has a low count because nobody was watching, not because it is
    quiet, and it gets no figure.
    """
    cur.execute(
        """
        SELECT o.id,
               count(*) FILTER (WHERE coalesce(a.published_at, a.fetched_at)
                                     > now() - %(window)s::interval),
               min(coalesce(a.published_at, a.fetched_at))
                   <= now() - %(window)s::interval
        FROM orgs o JOIN articles a ON a.org_id = o.id
        GROUP BY o.id
        """,
        {"window": f"{CADENCE_WINDOW_DAYS} days"},
    )
    return {org_id: (recent, full_window) for org_id, recent, full_window in cur.fetchall()}


def cadence_phrase(stats):
    """A publishing rate in plain words, or None where there isn't one.

    Withheld rather than extrapolated. An org we have not been collecting for
    a full window has nothing to average — a fortnight of crawling says
    nothing about the fortnight before it. An org that has published nothing
    lately is said to have published nothing, because "0 stories a week" is a
    rate, and the newsroom is not keeping to it; it has stopped.
    """
    if not stats:
        return None
    recent, full_window = stats
    if not full_window:
        return None
    if not recent:
        return "nothing published in the past month"
    per_week = recent / (CADENCE_WINDOW_DAYS / 7)
    if per_week < 0.75:
        # One or two in four weeks. A weekly rate here would round to nothing.
        if recent == 1:
            return "about a story a month"
        return f"about {COUNT_WORDS[recent]} stories a month"
    if per_week < 1.5:
        return "about a story a week"
    weekly = math.floor(per_week + 0.5)
    return f"about {COUNT_WORDS.get(weekly, weekly)} stories a week"


def cadence_line(a):
    """The cadence element of the rail, or nothing at all.

    A newsroom with no feed makes no cadence claim: whatever is in the
    archive for it came from somewhere else and is not a measure of how it
    publishes.
    """
    if not a.get("org_feed"):
        return ""
    phrase = cadence_phrase(CADENCE.get(a.get("org_id")))
    return f'<p class="cadence">{esc(phrase)}</p>' if phrase else ""


def render_feed_item(cur, a, mode="site", prefix="", with_related=True, skip_images=(),
                     eager=False):
    """Where it is, who published it, when, and then the story."""
    out = ["<article>"]

    # 1. a column down the right: section first, then whose state this
    #    newsroom is in and where in it, what kind of newsroom it is, how
    #    often it publishes, then the ask.
    column = []
    if a.get("subject"):
        label = esc(a["subject"])
        column.append(f'<span class="lozenge section">{label}</span>' if mode == "onepage"
                      else f'<a class="lozenge section" '
                           f'href="{subject_href(a["subject"], prefix)}">{label}</a>')
    # A trending topic is a tag that links out of the page, so the
    # self-contained one-pager leaves it off.
    if mode != "onepage":
        column.append(card_topics(a, prefix))
    column.append(state_identity(a, prefix if mode != "onepage" else ""))
    column.append(locator_map(a))
    column.append(region_line(a))
    column.append(tag_links(a, prefix if mode != "onepage" else "", cap=RAIL_TAG_CAP))
    column.append(cadence_line(a))
    column.append(support_link(a))
    out.append(f'<aside class="tagcol">{"".join(column)}</aside>')

    # 2. when it ran. With no date from the newsroom the only date we have is
    #    the day we picked the story up, which is a different claim and is
    #    labelled as one rather than passed off as publication.
    published = a["published_at"]
    moment = published or a["fetched_at"]
    dateline = local_dateline(moment, a.get("state"), a.get("timezone"))
    pub_time = local_time(moment, a.get("state"), a.get("timezone"))
    if dateline:
        stamp = (f'<time datetime="{moment.astimezone(timezone.utc).isoformat()}" '
                 f'data-pub="{esc(pub_time)}">{esc(dateline)}</time>')
        if published:
            out.append(f'<p class="whenwhere">{stamp}</p>')
        else:
            out.append(f'<p class="whenwhere nodate">'
                       f'<span class="collected">Collected by this site</span> {stamp}</p>')

    # 3. who published it. The publication name is itself the marker:
    #    opening it gives the newsroom in its own words without leaving the
    #    feed. Their site is the first link inside, so it is still one tap
    #    away, and the newsroom page is the one after it.
    out.append(disclosure(f'<strong>{esc(a["org_name"])}</strong>',
                          org_profile_panel(a, mode, prefix), "source"))

    # 4. headline
    out.append(f'<h2><a href="{esc(a["url"])}">{esc(tighten(a["title"]))}</a></h2>')

    # 5. byline. It opens the same way the masthead above it does — but only
    #    where there is a person behind it. A byline the site cannot resolve
    #    stays the plain line of text it has always been, rather than
    #    becoming a marker that opens onto nothing.
    if a.get("author"):
        who = reporter_of(a)
        out.append(disclosure(f'By <strong>{esc(who["name"])}</strong>',
                              reporter_panel(a), "byline")
                   if who else f'<p class="byline">By {esc(credit(a["author"]))}</p>')

    # 6. the photo
    if a.get("image_file") and a["image_file"] not in skip_images:
        # image_w/image_h are only missing on rows crawled before those
        # columns existed; the .shot img:not([width]) CSS rule reserves an
        # aspect-ratio box for those instead of explicit dimensions.
        size = ""
        if a.get("image_w") and a.get("image_h"):
            size = f' width="{a["image_w"]}" height="{a["image_h"]}"'
        alt = esc(a.get("image_alt") or "")
        caption = f"<figcaption>{alt}</figcaption>" if a.get("image_alt") else ""
        # The pictures already on screen when the page opens are not worth
        # deferring: lazy-loading them leaves the reserved box empty exactly
        # where someone is looking. The rest load as they are scrolled to.
        how = ('fetchpriority="high" decoding="async"' if eager
               else 'loading="lazy" decoding="async"')
        out.append(
            f'<figure class="shot">'
            f'<a href="{esc(a["url"])}" tabindex="-1" aria-hidden="true">'
            f'<img src="{esc(image_href(a["image_file"], prefix))}" alt="{alt}"{size} '
            f'{how}></a>'
            f'{caption}'
            f'</figure>'
        )

    # 7. the text, with Read more running on from the end of it
    summary = esc(tighten(clip_summary(a["summary"]))) if a.get("summary") else ""
    more = (f'<a class="lozenge more" href="{esc(a["url"])}">Read more '
            f'<span aria-hidden="true">&rarr;</span></a>')
    out.append(f'<p class="body">{summary} {more}</p>' if summary else f"<p>{more}</p>")

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
                skip_images=(), page_index=0, page_count=1, stem=None,
                intro="", show_heading=True):
    parts = []
    if page_index == 0:
        if show_heading:
            parts.append(f"<h1>{esc(heading)}</h1>")
        if intro:
            parts.append(intro)
    parts.append('<div id="feed-items">')
    with_picture = 0
    for a in articles:
        eager = False
        if a.get("image_file") and a["image_file"] not in skip_images:
            with_picture += 1
            eager = with_picture <= EAGER_IMAGES
        parts.append(render_feed_item(cur, a, mode, prefix, with_related, skip_images, eager))
    parts.append("</div>")
    if stem and page_index + 1 < page_count:
        parts.append(
            f'<p><a id="more" href="{feed_page_name(stem, page_index + 1)}">'
            f'More stories</a></p>'
        )
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


# The rail is a narrow column; a newsroom carrying half a dozen features
# would otherwise push its headline down a variable amount card to card.
RAIL_TAG_CAP = 3


def tag_links(org, prefix="", cap=None, after=0):
    """Tags as tappable lozenges, each leading to that characteristic's page.

    Ordered by TAG_PRIORITY (Ownership, then Community, then Practice, each
    group in its own fixed order) so a capped render always keeps the same
    subset, in the same order, as an uncapped one — and so `after` picks up
    exactly where a render capped at the same number left off.
    """
    tags = sorted(ownership_tags(org), key=lambda t: TAG_PRIORITY.get(t, len(TAG_PRIORITY)))
    tags = tags[after:]
    if cap is not None:
        tags = tags[:cap]
    if not tags:
        return ""
    links = "".join(
        f'<a class="lozenge" href="{prefix}features/{tag_slug(tag)}.html">{esc(tag)}</a>'
        for tag in tags
    )
    return f'<span class="tags">{links}</span>'


def feature_links(org, prefix=""):
    return " · ".join(
        f'<a href="{feature_href(f, prefix)}">{esc(f)}</a>' for f in (org.get("features") or [])
    )


def load_reporter_panels(cur, headlines=REPORTER_HEADLINES):
    """Every byline on the site that resolves to a person, in one query.

    The database groups by the byline exactly as it was written and counts,
    dates and collects the newest headlines in the same pass; the fold from
    written bylines to reporter identities happens here, because that is
    where the rules for who counts as a person live. What comes back is
    keyed by identity, so rendering a card is a dictionary lookup however
    many cards the build writes.

    Grouping case-sensitively is deliberate. A CMS that files one story
    under "Dana Reyes" and the next under "DANA REYES" would otherwise have
    the two spellings collapsed into one row inside the database, and
    whichever of them sorted first would be the name on the card; keeping
    them apart lets the merge below prefer the one that is not shouted.
    """
    cur.execute(
        """
        WITH byline AS (
            SELECT btrim(a.author) AS author, a.title, a.url,
                   o.name AS org_name,
                   coalesce(a.published_at, a.fetched_at) AS at
            FROM articles a JOIN orgs o ON o.id = a.org_id
            WHERE a.author IS NOT NULL AND btrim(a.author) <> ''
        ), ranked AS (
            SELECT byline.*,
                   count(*) OVER (PARTITION BY author) AS n_stories,
                   row_number() OVER (PARTITION BY author ORDER BY at DESC) AS rn
            FROM byline
        )
        SELECT author, min(n_stories), min(at), max(at),
               array_agg(DISTINCT org_name),
               json_agg(json_build_object('title', title, 'url', url,
                                          'ts', extract(epoch FROM at))
                        ORDER BY at DESC) FILTER (WHERE rn <= %s)
        FROM ranked GROUP BY author
        """,
        (headlines,),
    )
    panels = {}
    for author, n_stories, first_at, last_at, newsrooms, recent in cur.fetchall():
        key = reporters.reporter_key(author)
        if not key:
            continue
        name = reporters.reporter_name(author)
        who = panels.get(key)
        if who is None:
            who = panels[key] = {
                "name": name, "n_stories": 0,
                "first_at": first_at, "last_at": last_at,
                "newsrooms": set(), "recent": [],
            }
        # Feeds disagree about capitals, and the database is under no
        # obligation to hand the groups back in the same order twice. Prefer
        # the spelling that is not shouted, and settle ties alphabetically,
        # so one reporter is not named two ways from one build to the next.
        if (name.isupper(), name) < (who["name"].isupper(), who["name"]):
            who["name"] = name
        who["n_stories"] += n_stories
        who["first_at"] = min(who["first_at"], first_at)
        who["last_at"] = max(who["last_at"], last_at)
        who["newsrooms"].update(newsrooms or ())
        who["recent"].extend(recent or ())
    for who in panels.values():
        who["newsrooms"] = sorted(who["newsrooms"])
        who["recent"] = sorted(who["recent"],
                               key=lambda r: (-r["ts"], r["title"]))[:headlines]
    return panels
