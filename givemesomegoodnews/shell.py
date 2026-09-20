"""The page around the writing: one stylesheet, a masthead, a menu, a footer.

Every page on the site is this shell with something in the middle, which is
why the type and spacing are decided once, here, and why the four small
scripts live here too — each is an enhancement the page works without.
"""

from html import escape as esc

from . import config
from .topics import topic_bar


# Subjects come first because they are what a reader is actually choosing
# between; then the ways of navigating the whole thing; then the meta pages.
SUBJECT_ORDER = [
    "News", "Politics", "Opinion", "Health", "Environment", "Education",
    "Business", "Housing", "Sports", "Food", "Arts",
]


NAV_BROWSE = [
    ("trending.html", "Trending"),
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
@font-face{{font-family:Text;src:url({prefix}fonts/ibm-plex-serif-400.woff2) format('woff2');
font-weight:400;font-display:swap}}
@font-face{{font-family:Text;src:url({prefix}fonts/ibm-plex-serif-600.woff2) format('woff2');
font-weight:600;font-display:swap}}
@font-face{{font-family:Text;src:url({prefix}fonts/ibm-plex-serif-700.woff2) format('woff2');
font-weight:700;font-display:swap}}
:root{{--fg:#111;--bg:#fff;--dim:#555;--rule:#ddd;--link:#c8102e;--seen:#8c0b20;
--band:#f6f6f4}}
@media(prefers-color-scheme:dark){{
:root{{--fg:#e8e8e8;--bg:#111;--dim:#a6a6a6;--rule:#333;--link:#ff6b6b;--seen:#cf8f8f;
--band:#181818}}}}
html{{-webkit-text-size-adjust:100%}}
body{{font:400 1.0625rem/1.62 Text,Georgia,serif;color:var(--fg);background:var(--bg);
max-width:40rem;margin:0 auto;padding:1rem 0 4rem;overflow-wrap:break-word}}
header,main>h1,main>p,main>ul,main>h2,main>h3,main>figure,footer,#feed-items>p,
main>form,main>table,main>blockquote,main>hr,main>nav,main>div>h1,main>div>h2
{{padding-left:1rem;padding-right:1rem}}
h1,h2,h3{{font-family:Text,Georgia,serif;letter-spacing:-.004em}}
.sans{{font-family:Plex,system-ui,sans-serif}}
a{{color:var(--link);text-decoration:none}}
a:visited{{color:var(--seen)}}
a:hover,a:focus{{text-decoration:underline}}
:focus-visible{{outline:3px solid var(--link);outline-offset:2px}}
.skip{{position:absolute;left:-9999px}}
.skip:focus{{position:static;display:block;padding:.5rem 0}}
h1{{font-size:1.5rem;line-height:1.2;margin:1rem 0}}
h2{{font-size:1.2rem;line-height:1.25;margin:.2rem 0 .4rem}}
/* The headline owns the space around it: the rows above and below sit
   flush so the gap is always exactly this, on every item. */
article h2{{font-family:Text,Georgia,serif;font-size:1.6rem;font-weight:600;
line-height:1.25;margin:1rem 0 .9rem;max-width:75%}}
@media(max-width:34rem){{article h2{{max-width:100%;font-size:1.4rem}}}}
/* Same short measure as the headline, so the paragraph doesn't run the
   full width of the card just because the tag column has ended above it. */
article .body{{max-width:75%}}
@media(max-width:34rem){{article .body{{max-width:100%}}}}
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
/* The box is reserved before the picture arrives so nothing below it moves
   when it does. Tinted rather than left white: an empty white rectangle mid
   story reads as something broken, a pale block reads as a picture coming. */
.shot img{{width:100%;border:1px solid var(--rule);background:var(--band)}}
/* Rows crawled before image_w/image_h existed have no dimensions to set
   as attributes; reserve a box for them anyway so the layout doesn't
   jump once the image loads. */
.shot img:not([width]){{aspect-ratio:3/2;object-fit:cover}}
.shot figcaption{{font:400 .78rem/1.4 PlexMono,ui-monospace,monospace;color:var(--dim);
margin:.3rem 0 0}}
@media(max-width:34rem){{.shot{{width:40%}}}}
time[data-pub]{{cursor:pointer}}
.yours{{color:var(--dim)}}
.lozenge{{display:inline-block;font:400 .72rem/1 PlexMono,ui-monospace,monospace;
padding:.25rem .5rem;margin:0 .3rem .3rem 0;border:1px solid var(--rule);border-radius:1rem;
color:var(--dim);text-decoration:none}}
.lozenge:hover,.lozenge:focus{{border-color:var(--link);color:var(--link);text-decoration:none}}
.lozenge[aria-current=page],.lozenge.on{{border-color:var(--link);color:var(--bg);background:var(--link)}}
.chips{{margin:.75rem 0 1.25rem}}
/* A trending topic is a tag like the others, marked with the site's red dot.
   The dot is drawn, not written, so it adds nothing to the link's name. */
.lozenge.topic{{color:var(--fg)}}
.lozenge.topic::before{{content:"";display:inline-block;width:.45em;height:.45em;
border-radius:50%;background:var(--link);margin-right:.4em;vertical-align:.08em}}
.lozenge.topic.on,.lozenge.topic[aria-current=page]{{color:var(--bg)}}
.lozenge.topic.on::before,.lozenge.topic[aria-current=page]::before{{background:var(--bg)}}
/* The topics under the masthead: one row that scrolls sideways on a phone
   rather than wrapping into a wall of tags above every page. */
.trendbar{{display:flex;align-items:center;gap:.35rem;overflow-x:auto;
margin:-.6rem 0 1rem;padding:0 1rem .35rem;scrollbar-width:thin;
-webkit-mask-image:linear-gradient(to right,#000 calc(100% - 2.5rem),transparent);
mask-image:linear-gradient(to right,#000 calc(100% - 2.5rem),transparent)}}
.trendbar .lozenge{{flex:none;margin:0;white-space:nowrap}}
.trendlabel{{flex:none;font:600 .68rem/1 PlexMono,ui-monospace,monospace;
text-transform:uppercase;letter-spacing:.06em;color:var(--fg);margin-right:.25rem}}
a.trendlabel:visited{{color:var(--fg)}}
/* The front page's section: topics in a grid, each with its reach and the
   story at its centre. */
.trending-now{{margin:.5rem 0 1.25rem;padding:1rem;background:var(--band);
border-top:2px solid var(--fg);border-bottom:1px solid var(--rule)}}
.trending-now h2{{font:600 .8rem/1.4 PlexMono,ui-monospace,monospace;color:var(--fg);
text-transform:uppercase;letter-spacing:.06em;margin:0 0 .75rem}}
.trending-now ol{{list-style:none;padding:0;margin:0 0 .75rem;display:grid;
grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:1rem 1.5rem}}
.trending-now li{{margin:0;display:flex;flex-direction:column;gap:.2rem}}
.trending-now .name{{font:600 1.2rem/1.25 Text,Georgia,serif}}
.trending-now .name::before{{content:"";display:inline-block;width:.4em;height:.4em;
border-radius:50%;background:var(--link);margin-right:.45em;vertical-align:.15em}}
.trending-now .lead{{font:400 .95rem/1.4 Text,Georgia,serif;color:var(--fg)}}
.trending-now>p{{margin:0}}
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
.tags{{display:flex;flex-wrap:wrap;gap:.3rem}}
.tagcol .tags{{justify-content:flex-end}}
/* Whose state this is: the flag small, and the state's own code beside it
   in type, because at this size the code is what reads. The rule around the
   flag keeps a pale one (Rhode Island's white field) from dissolving into
   the page. */
.ident{{display:flex;align-items:center;gap:.35rem;margin:0}}
.flag{{flex:none;border:1px solid var(--rule);background:var(--bg)}}
/* Where the newsroom is: the state's outline with one mark on it, and the
   same answer in words underneath for anyone not seeing the picture. */
.locator{{display:block;margin:.1rem 0}}
.locator .state{{fill:var(--band);stroke:var(--dim);stroke-width:.6;stroke-linejoin:round}}
.locator .state.whole{{fill:var(--link);fill-opacity:.3}}
.locator .near{{fill:var(--link);fill-opacity:.35}}
.locator .here{{fill:var(--link);stroke:var(--bg);stroke-width:.7}}
.region{{font:400 .72rem/1.35 PlexMono,ui-monospace,monospace;color:var(--dim);
margin:0;max-width:100%;text-align:right;overflow-wrap:break-word}}
/* This line is now the card's only statement of place, and its two halves
   are the searches for that city and that state. They stay the colour of
   the caption rather than turning the rail red — a rule under the words is
   enough to say they are links, and the red is kept for the hover and for
   the ask at the foot of the column. */
.region a{{color:inherit;text-decoration:underline;
text-decoration-color:var(--rule);text-underline-offset:.15em}}
.region a:hover,.region a:focus{{color:var(--link);
text-decoration-color:currentColor}}
/* How often they publish, in words. Same quiet caption voice as the region
   line above it, italic so it reads as a note about the newsroom rather
   than another piece of its address. Both are omitted outright where the
   answer would be a guess, so neither leaves a labelled gap behind. */
.cadence{{font:italic 400 .72rem/1.35 PlexMono,ui-monospace,monospace;
color:var(--dim);margin:0;max-width:100%;text-align:right;
overflow-wrap:break-word}}
.lozenge.give{{border-color:var(--link);color:var(--link);font-weight:600}}
.lozenge.give:hover,.lozenge.give:focus{{background:var(--link);color:var(--bg)}}
/* The ask closes the rail, so it gets a little air above it rather than
   sitting flush against the cadence line. Where there is no payment page to
   send anyone to it is not rendered, and the rail simply ends earlier. */
.tagcol .give{{margin-top:.2rem}}
a.lozenge.more{{white-space:nowrap;border-color:var(--fg);color:var(--fg);font-weight:600}}
a.lozenge.more:hover,a.lozenge.more:focus{{background:var(--fg);color:var(--bg)}}
/* A newsroom's name can run to sixty characters, and the tag column takes
   a third of the width, so the name breaks rather than pushing out. */
.source{{font-family:Plex,system-ui,sans-serif;font-size:1rem;margin:0 0 .3rem;
overflow-wrap:break-word}}
.whenwhere{{font:400 .8rem/1.4 PlexMono,ui-monospace,monospace;color:var(--dim);
margin:0 0 .2rem;display:flex;flex-wrap:wrap;align-items:center;gap:.35rem}}
/* A collected date is not a publication date and should not read like one.
   Weight rather than a lozenge: the lozenges on this page are all links,
   and this is a label. */
.whenwhere .collected{{color:var(--fg);font-weight:600;white-space:nowrap}}
.byline{{font-family:Plex,system-ui,sans-serif;font-size:.9rem;color:var(--dim);
margin:0 0 .8rem}}
/* Inline disclosures: a marker beside a name, its panel opening in place.
   Native <details>, like the burger menu — it works with scripting off,
   takes keyboard focus, and announces expanded/collapsed by itself.
   A disclosure carries the class of whatever it replaced — .source, .byline
   — and takes its type and its spacing from that, so a byline with a
   profile behind it sits on exactly the rhythm one without a profile does.
   A <summary> keeps its own display:list-item. Overriding it is what costs
   the element its disclosure semantics in some browsers — the marker would
   stop announcing expanded and collapsed — so anything the marker needs of
   its own hangs on a span inside the summary rather than on the summary. */
.disc>summary{{cursor:pointer;list-style:none;color:var(--fg)}}
.disc>summary::-webkit-details-marker{{display:none}}
.disc>summary:hover,.disc>summary:focus{{color:var(--link)}}
/* The summary spans the column, the marker does not: put the focus ring
   around what a keyboard reader is actually pointing at. */
.disc>summary:focus-visible{{outline:none}}
.disc>summary:focus-visible .disc-line{{outline:3px solid var(--link);outline-offset:3px}}
/* The name and the cue are a run of inline text rather than a row laid out
   as a box: laying them out together — an inline-flex, say — makes one
   atomic inline box, and a box too wide for what the tag column leaves of
   the line is set below the float entire, which stranded the whole story,
   headline and all, under the rail on a phone. Inline, the line breaks
   between the name and the cue and the card goes on flowing beside the
   column, so the space before the cue is this margin and not a gap. */
.disc-cue{{display:inline-block;vertical-align:baseline;margin-left:.4rem;
font:400 .68rem/1 PlexMono,ui-monospace,monospace;color:var(--dim);
border:1px solid var(--rule);border-radius:1rem;padding:.24rem .45rem;
white-space:nowrap;text-transform:uppercase;letter-spacing:.05em}}
/* The caret is drawn rather than written: a glyph in the content of a
   pseudo-element joins the summary's accessible name, and "black
   down-pointing small triangle" read out after every masthead is noise on
   top of the expanded/collapsed the browser already announces. */
.disc-cue::after{{content:"";display:inline-block;width:0;height:0;margin-left:.45em;
border:.32em solid transparent;border-top-color:currentColor;vertical-align:-.07em}}
.disc[open]>summary .disc-cue::after{{border-top-color:transparent;
border-bottom-color:currentColor;vertical-align:.25em}}
.disc>summary:hover .disc-cue,.disc>summary:focus-visible .disc-cue
{{border-color:var(--link);color:var(--link)}}
/* The panel opens below the marker and nothing else moves: the card grows
   downwards, so what is above it keeps the position the reader left it in. */
.disc-panel{{border-left:3px solid var(--rule);margin:.5rem 0 .7rem;padding-left:.8rem;
font-family:Plex,system-ui,sans-serif;font-size:.9rem}}
.disc-panel blockquote{{margin:0 0 .35rem;padding:0;border:0}}
.disc-panel p{{margin:0 0 .45rem}}
/* Motion only where it is asked for: under reduce, nothing here applies. */
@media(prefers-reduced-motion:no-preference){{
.disc[open]>.disc-panel{{animation:disc-open .18s ease-out}}
@keyframes disc-open{{from{{opacity:0}}to{{opacity:1}}}}}}
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

    return f"""<a class="home" href="/"><img class="masthead"
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
    items = [("/", "Today's news")] + NAV_BROWSE + NAV_META
    return " \u00b7 ".join(
        f'<a href="{href(t) if not t.startswith(prefix) else t}">{esc(label)}</a>'
        for t, label in items
    )


def share_card(title, description):
    """What a link to this page unfurls into when somebody posts it.

    One picture for the whole site rather than one per page: the card is
    the masthead over the map of who is in the catalog, which is as true of
    a tag page as it is of the front page. The image has to be an absolute
    URL — a scraper resolves nothing — and a PNG, because none of the
    platforms render SVG.

    No og:url: this shell does not know which path it is being written to,
    and a canonical link that is wrong on every page but one is worse than
    none. Scrapers fall back to the URL they fetched, which is right.
    """
    image = f"{config.SITE_URL}/{config.SHARE_IMAGE}"
    width, height = config.SHARE_IMAGE_SIZE
    return "\n".join((
        '<meta property="og:type" content="website">',
        f'<meta property="og:site_name" content="{esc(config.SITE_NAME)}">',
        f'<meta property="og:title" content="{esc(title)}">',
        f'<meta property="og:description" content="{esc(description)}">',
        f'<meta property="og:image" content="{esc(image)}">',
        f'<meta property="og:image:type" content="image/png">',
        f'<meta property="og:image:width" content="{width}">',
        f'<meta property="og:image:height" content="{height}">',
        f'<meta property="og:image:alt" content="{esc(config.SHARE_IMAGE_ALT)}">',
        '<meta name="twitter:card" content="summary_large_image">',
    ))


def page(title, body, prefix="", nav_html=None, scripts="", description="",
         feed_href="feed.xml", feed_title=None, current_topic=None):
    # A page with nothing more particular to say describes itself the way
    # the site does, rather than going out with no description at all.
    description = description or config.SITE_DESCRIPTION
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
{share_card(title, description)}
<link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">
<link rel="alternate" type="application/rss+xml"
 title="{esc(feed_title or config.SITE_NAME)}" href="{prefix}{feed_href}">
{stylesheet(prefix)}
</head>
<body>
<a class="skip" href="#main">Skip to the stories</a>
<header>
{nav_html or menu(prefix, config.SITE_NAME)}
</header>
{"" if nav_html else topic_bar(prefix, current_topic)}
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
{IMAGE_SCRIPT}
{scripts}
</body>
</html>
"""


def search_form(query=""):
    """Plain GET form — search works with JavaScript switched off."""
    return (
        '<form action="/search" method="get" role="search">'
        f'<p><input type="search" name="q" value="{esc(query)}" '
        'placeholder="Search headlines and summaries" aria-label="Search"> '
        '<button type="submit" aria-label="Search">&rarr;</button></p></form>'
    )


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


IMAGE_SCRIPT = """<script>
/* A picture that fails to load leaves a reserved box with nothing in it.
   Nothing can be done about that in CSS, so the figure goes; the card is
   the same one it would have been had the story never carried a picture.
   Delegated and capturing, because error does not bubble. */
document.addEventListener("error", function (event) {
  var img = event.target;
  if (!img || img.tagName !== "IMG") return;
  var shot = img.closest && img.closest("figure.shot");
  if (shot) shot.remove();
}, true);
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
