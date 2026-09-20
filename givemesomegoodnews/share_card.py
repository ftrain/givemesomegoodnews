"""The picture a link to this site unfurls into, drawn from the catalog.

A share card is the one page of this site most people see: it is what a
link to it looks like in a message, a post or a chat window, and without
one the link is a grey rectangle with a domain in it. So this draws the
site itself — the masthead, and every newsroom in the catalog as a dot on
the same Albers composite the coverage map uses — rather than a logo on a
coloured field.

It is an asset, not a page. The build that runs every fifteen minutes
copies assets/share.png into the site the way it copies the flags; nothing
redraws it, and no build ever starts a browser. Run this when the catalog
has grown enough that the map would look different, and commit the PNG:

    python3 -m givemesomegoodnews.share_card

The card is laid out as a page — the site's own type, colours and rules, at
poster size — and photographed by whatever headless Chrome is on the
machine. The page itself is written to a temporary directory and thrown
away: it is this module, and nothing should be edited in the copy. Social
scrapers render no SVG, so a PNG is what ships. With no browser to be
found, nothing is written and the card already in the repository stands.
"""

import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config, geocode
from .albers import MapProjection
from .seed import load_orgs

# What the platforms ask for: 1200x630 is the size Facebook, LinkedIn,
# Slack and X all crop least from, and twitter:card=summary_large_image
# shows at very nearly this shape.
WIDTH, HEIGHT = config.SHARE_IMAGE_SIZE

# The site's own palette, from the stylesheet's light theme. A share card
# has no dark mode to switch to — it is a picture — so it is the light one.
FG, DIM, RULE, LINK, BAND, BG = "#111", "#555", "#ddd", "#c8102e", "#f6f6f4", "#fff"

# The site's own words about itself, which the pages put in their
# description as well: the card and the text under it say the same thing.
TAGLINE = config.SITE_DESCRIPTION

MAP_WIDTH = 620
MARGIN = 54


def newsroom_points(proj):
    """Every newsroom the map can place, as a point on it.

    Coordinates come from the catalog where it has them and from the
    gazetteer where it does not, which is the same pair of answers the
    seeder puts in the database. A newsroom with neither — a national desk
    with no address — is not on the map, because there is nowhere honest to
    put it.
    """
    seen = set()
    points = []
    for org in load_orgs():
        state = (org.get("state") or "").upper()
        lat, lon = org.get("lat"), org.get("lon")
        if (lat is None or lon is None) and state:
            lat, lon, _precision = geocode.lookup(org.get("city"), state)
        if lat is None or lon is None or not state or not proj.mappable(state):
            continue
        x, y = proj.to_svg_coords(float(lon), float(lat), state)
        # Two newsrooms in one town are one dot: at this size the second is
        # drawn exactly on top of the first, and stacking translucent dots
        # only makes the cities look like ink blots.
        key = (round(x), round(y))
        if key in seen:
            continue
        seen.add(key)
        points.append(key)
    return points


def map_svg():
    """The country, shaded, with a dot on every town that has a newsroom."""
    proj = MapProjection(config.STATES_GEOJSON, width=MAP_WIDTH, pad=6)
    states = "".join(
        f'<path d="{d}" fill="{BAND}" stroke="{RULE}" stroke-width=".8" '
        'stroke-linejoin="round"/>'
        for _name, d in proj.state_paths()
    )
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="3.1" fill="{LINK}"/>'
                   for x, y in newsroom_points(proj))
    return (f'<svg class="map" viewBox="0 0 {proj.width} {proj.height}" '
            f'width="{proj.width}" height="{proj.height}" '
            f'xmlns="http://www.w3.org/2000/svg">{states}{dots}</svg>')


def wordmark_svg():
    """The masthead's letterforms, which are paths and need no font."""
    source = (config.ASSETS_DIR / "masthead.svg").read_text()
    box = re.search(r'viewBox="([^"]+)"', source).group(1)
    path = re.search(r'<path d="([^"]+)"', source).group(1)
    # The masthead file carries a dark-mode rule of its own; the card is a
    # flat picture, so the fill is set here instead of inherited from it.
    return (f'<svg class="wordmark" viewBox="{box}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="Give Me Some Good News">'
            f'<path d="{path}" fill="{FG}"/></svg>')


def card_html(font_dir=None):
    """The card as a page: the same type, the same colours, at poster size."""
    fonts = Path(font_dir or (config.ASSETS_DIR / "fonts")).resolve()
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{config.SITE_NAME} — share card</title>
<style>
@font-face{{font-family:Text;src:url("{fonts / 'ibm-plex-serif-400.woff2'}") format('woff2');
font-weight:400}}
@font-face{{font-family:PlexMono;src:url("{fonts / 'ibm-plex-mono.woff2'}") format('woff2');
font-weight:400}}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{WIDTH}px;height:{HEIGHT}px;background:{BG};color-scheme:light}}
.card{{width:{WIDTH}px;height:{HEIGHT}px;padding:{MARGIN}px;display:flex;
flex-direction:column;gap:30px;background:{BG}}}
.wordmark{{display:block;width:100%;height:auto}}
.rule{{height:3px;background:{LINK}}}
.body{{flex:1;display:flex;align-items:center;gap:44px}}
.map{{flex:none;display:block}}
.say{{display:flex;flex-direction:column;gap:22px}}
.tagline{{font:400 34px/1.38 Text,Georgia,serif;color:{FG}}}
.where{{font:400 23px/1 PlexMono,ui-monospace,monospace;color:{LINK}}}
.credit{{font:400 18px/1.4 PlexMono,ui-monospace,monospace;color:{DIM}}}
</style>
</head>
<body>
<div class="card">
{wordmark_svg()}
<div class="rule"></div>
<div class="body">
{map_svg()}
<div class="say">
<p class="tagline">{TAGLINE}</p>
<p class="where">{config.SITE_URL.split('//')[-1]}</p>
<p class="credit">One dot per town.</p>
</div>
</div>
</div>
</body>
</html>
"""


def find_browser():
    """A headless Chrome to photograph the card with, if there is one."""
    named = os.environ.get("CHROME") or os.environ.get("CHROMIUM")
    if named and Path(named).is_file():
        return named
    # The headless shell first: it is the build made for exactly this, and
    # a full Chrome asked for a screenshot in the newer headless mode can
    # sit there until it is killed.
    for pattern in ("chromium_headless_shell-*/chrome-headless-shell-linux*/"
                    "chrome-headless-shell",
                    "chromium-*/chrome-linux*/chrome"):
        for found in sorted(glob.glob(str(Path.home() / ".cache/ms-playwright" / pattern))):
            if os.access(found, os.X_OK):
                return found
    for name in ("chrome-headless-shell", "chromium", "chromium-browser",
                 "google-chrome", "google-chrome-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def rasterise(html_path, png_path, browser=None):
    """Photograph the card. True if a PNG came out of it."""
    browser = browser or find_browser()
    if not browser:
        return False
    # The shell is headless by definition; a full Chrome has to be told, and
    # told the old way, which is the mode --screenshot still works in.
    mode = [] if "headless-shell" in Path(browser).name else ["--headless=old"]
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [browser, *mode, "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             f"--user-data-dir={profile}",
             f"--window-size={WIDTH},{HEIGHT}",
             f"--screenshot={png_path}",
             Path(html_path).resolve().as_uri()],
            check=True, capture_output=True, timeout=180,
        )
    return Path(png_path).is_file()


def main():
    png_path = config.ASSETS_DIR / config.SHARE_IMAGE
    browser = find_browser()
    if not browser:
        print("no headless Chrome here — set CHROME to one and run again; "
              f"{png_path.name} is unchanged", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as work:
        html_path = Path(work) / "share-card.html"
        html_path.write_text(card_html())
        rasterise(html_path, png_path, browser)
    print(f"wrote {png_path} ({png_path.stat().st_size // 1024} KB) "
          f"with {Path(browser).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
