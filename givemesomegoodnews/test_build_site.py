"""Tests for the inline disclosure on a feed card.

Run with `python3 -m unittest givemesomegoodnews.test_build_site`. Nothing
here touches the database: `render_feed_item` is given `with_related=False`,
which is the same thing `searchd` does, so no cursor is ever used.
"""

import contextlib
import os
import pathlib
import re
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from PIL import Image

from . import cards, config, images, links, migrate_images, pages, prose, prune
from . import share_card, shell, syndicate
from . import reporters as rp
from . import searchd


ABOUT = (
    "The Ledger is a worker-owned newsroom covering the county from a "
    "storefront on Main Street. We were founded by four reporters who left "
    "the daily when it was sold, and we are paid for by our readers rather "
    "than by anyone with business before the council. Everything we publish "
    "is free to read, and everything we publish is reported here.\n\n"
    "We also run a weekly print edition."
)


def article(**over):
    a = {
        "id": 1,
        "url": "https://ledger.example/story",
        "title": "County buys the old mill",
        "summary": "The council voted 5-2.",
        "author": "Dana Reyes",
        "published_at": datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
        "image_file": None, "image_w": None, "image_h": None, "image_alt": None,
        "subject": "News",
        "org_name": "The Ledger", "slug": "the-ledger",
        "org_url": "https://ledger.example/",
        "support_url": "https://ledger.example/donate", "support_label": "Donate",
        "state": "VT", "city": "Rutland", "beat": None,
        "coverage": "Rutland County",
        "coverage_type": "city", "timezone": None,
        "model": "worker-owned cooperative",
        "features": ["Worker-owned", "Reader-funded", "INN member"],
        "org_feed": None, "in_default": True, "language": "English",
        "about_text": ABOUT,
    }
    a.update(over)
    return a


def card(**over):
    return cards.render_feed_item(None, article(**over), with_related=False)


def reporter(**over):
    who = {
        "name": "Dana Reyes", "n_stories": 7,
        "first_at": datetime(2025, 6, 1, tzinfo=timezone.utc),
        "last_at": datetime(2026, 6, 1, tzinfo=timezone.utc),
        "newsrooms": ["The Ledger", "VTDigger"],
        "recent": [
            {"title": "County buys the old mill",
             "url": "https://ledger.example/story", "ts": 3.0},
            {"title": "Council splits on the budget",
             "url": "https://ledger.example/budget", "ts": 2.0},
        ],
    }
    who.update(over)
    return who


@contextlib.contextmanager
def reporters_loaded(**by_key):
    """What main() leaves in place before anything renders."""
    cards.REPORTERS.clear()
    cards.REPORTERS.update(by_key)
    try:
        yield
    finally:
        cards.REPORTERS.clear()


class FakeCursor:
    """Enough of a cursor to count how many queries a build costs."""

    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    """Enough of a connection for `with connect() as conn, conn.cursor() as cur`."""

    def __init__(self, cursor):
        self.cursor_obj = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cursor_obj


class DisclosureHelper(unittest.TestCase):
    def test_wraps_marker_and_panel_in_details(self):
        html = cards.disclosure("<strong>X</strong>", "<p>hello</p>")
        self.assertIn('<details class="disc">', html)
        self.assertIn('<summary><span class="disc-line"><strong>X</strong>', html)
        self.assertIn('<div class="disc-panel"><p>hello</p></div>', html)

    def test_no_marker_when_there_is_nothing_to_disclose(self):
        self.assertEqual(cards.disclosure("<strong>X</strong>", ""), "")

    def test_extra_class_joins_the_base_class(self):
        html = cards.disclosure("m", "<p>p</p>", "source")
        self.assertIn('<details class="disc source">', html)


class PublicationPanel(unittest.TestCase):
    def test_card_carries_a_marker_beside_the_publication_name(self):
        html = card()
        self.assertIn('<details class="disc source">', html)
        marker = re.search(r"<summary>(.*?)</summary>", html, re.S).group(1)
        self.assertIn("<strong>The Ledger</strong>", marker)
        self.assertIn('<span class="disc-cue">Profile</span>', marker)

    def test_panel_shows_description_coverage_and_two_links(self):
        html = card()
        panel = re.search(r'<div class="disc-panel">(.*?)</div>', html, re.S).group(1)
        self.assertIn("worker-owned newsroom covering the county", panel)
        self.assertIn("Covers Rutland County.", panel)
        self.assertIn('href="https://ledger.example/">Their site</a>', panel)
        self.assertIn('href="orgs/the-ledger.html">Newsroom page</a>', panel)

    def test_panel_repeats_nothing_the_rail_is_already_showing(self):
        # The panel opens beside the rail, not in place of it: a tag or an
        # ask rendered in both is the same lozenge printed twice, an inch
        # apart, and reads as two different ones.
        html = card()
        panel = re.search(r'<div class="disc-panel">(.*?)</div>', html, re.S).group(1)
        rail = re.search(r'<aside class="tagcol">(.*?)</aside>', html, re.S).group(1)
        for tag in re.findall(r'class="lozenge" href="features/[^"]*">([^<]+)<', rail):
            self.assertNotIn(f">{tag}</a>", panel)
        self.assertIn("ledger.example/donate", rail)
        self.assertNotIn("ledger.example/donate", panel)

    def test_panel_carries_the_tags_the_rail_had_no_room_for(self):
        many = [f"Tag{n}" for n in range(9)]
        a = article(features=many, model="")
        panel = cards.org_profile_panel(a)
        rail = cards.tag_links(a, cap=cards.RAIL_TAG_CAP)
        for tag in many[:cards.RAIL_TAG_CAP]:
            self.assertIn(f">{tag}</a>", rail)
            self.assertNotIn(f">{tag}</a>", panel)
        for tag in many[cards.RAIL_TAG_CAP:]:
            self.assertIn(f">{tag}</a>", panel)

    def test_unusable_about_text_still_gets_a_panel(self):
        for about in (None, "", "Too short to be anybody's About page."):
            panel = cards.org_profile_panel(article(about_text=about))
            self.assertNotIn("<blockquote>", panel)
            self.assertIn("Covers Rutland County.", panel)
            self.assertIn("Their site</a>", panel)
            self.assertIn("Newsroom page</a>", panel)

    def test_quote_is_an_excerpt_not_the_whole_about_page(self):
        panel = cards.org_profile_panel(article())
        quote = re.search(r"<blockquote><p>(.*?)</p></blockquote>", panel, re.S).group(1)
        self.assertLessEqual(len(quote), 430)
        self.assertNotIn("weekly print edition", quote)


class AboutOpening(unittest.TestCase):
    def test_skips_a_heading_or_stray_line_to_reach_the_description(self):
        text = "About The Ledger\n\nPublished: June 30, 2025\n\n" + ABOUT
        self.assertTrue(prose.about_opening(text).startswith("The Ledger is a worker-owned"))

    def test_truncates_a_long_paragraph(self):
        got = prose.about_opening("word " * 300)
        self.assertLessEqual(len(got), 430)
        self.assertTrue(got.endswith("[…]"))

    def test_nothing_from_an_unusable_about_text(self):
        for text in (None, "", "Short.", "About Us\n\nRead more"):
            self.assertEqual(prose.about_opening(text), "")

    def test_prefix_reaches_the_org_page_from_a_subdirectory(self):
        panel = cards.org_profile_panel(article(), prefix="../")
        self.assertIn('href="../orgs/the-ledger.html"', panel)

    def test_onepage_stays_self_contained(self):
        panel = cards.org_profile_panel(article(), mode="onepage")
        self.assertNotIn("orgs/the-ledger.html", panel)
        self.assertIn('href="https://ledger.example/">Their site</a>', panel)

    def test_disclosure_adds_no_script(self):
        self.assertNotIn("<script", card())

    def test_disclosure_opens_between_the_date_and_the_headline(self):
        html = card()
        self.assertLess(html.index('class="whenwhere"'), html.index('class="disc source"'))
        self.assertLess(html.index('class="disc source"'), html.index("<h2>"))


class PlaceOnTheCard(unittest.TestCase):
    def test_the_card_names_its_place_once_and_in_the_rail(self):
        # It used to name it four times: the state and the city as lozenges
        # above the headline, then the flag, the map and these words.
        html = card()
        self.assertNotIn('class="places"', html)
        self.assertNotIn("lozenge place", html)
        region = re.search(r'<p class="region">(.*?)</p>', html, re.S).group(1)
        self.assertIn("Rutland", region)
        self.assertIn("Vermont", region)

    def test_the_region_line_carries_the_searches_the_lozenges_had(self):
        region = re.search(r'<p class="region">(.*?)</p>', card(), re.S).group(1)
        self.assertIn('href="/search?place=Rutland&amp;state=VT"', region)
        self.assertIn('href="/search?state=VT"', region)

    def test_a_national_outlet_is_not_told_twice_that_it_is_national(self):
        # No flag to fly and no marker in its place either: the region line
        # is the one statement of where, and it is the link as well.
        html = card(coverage_type="national", coverage=None, state=None, city=None)
        self.assertNotIn('class="ident"', html)
        region = re.search(r'<p class="region">(.*?)</p>', html, re.S).group(1)
        self.assertIn('href="/search?national=1">National</a>', region)
        self.assertEqual(html.count("National"), 1)

    def test_the_words_are_the_same_words_the_locator_had(self):
        # region_name is what the line says with the links taken off; the
        # text edition and anything else reading it get the old string.
        self.assertEqual(cards.region_name(article()), "Rutland, Vermont")
        self.assertEqual(cards.region_name(article(geo_precision="county")),
                         "Rutland area, Vermont")


class ImageCacheLayout(unittest.TestCase):
    """Pictures live in site/img/<first two of the name>/<name>."""

    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        patch = mock.patch.object(images, "cache_dir", lambda: self.dir)
        patch.start()
        self.addCleanup(patch.stop)

    def name(self, n="ab12cd"):
        return n + ".webp"

    def test_a_pages_picture_url_carries_its_subdirectory(self):
        self.assertEqual(links.image_href("ab12cd.webp"), "img/ab/ab12cd.webp")
        self.assertEqual(links.image_href("ab12cd.webp", "../"), "../img/ab/ab12cd.webp")

    def test_the_rss_url_matches_the_page_url(self):
        article = {"url": "https://x.example/a", "title": "T", "published_at": None,
                   "fetched_at": None, "org_name": "Org", "org_url": "https://x.example/",
                   "image_file": "ab12cd.webp"}
        item = syndicate._item(article, "https://site.example")
        self.assertIn("https://site.example/img/ab/ab12cd.webp", item)

    def test_a_file_is_written_into_its_subdirectory(self):
        path = images.path_for(self.name(), write=True)
        self.assertEqual(path, self.dir / "ab" / "ab12cd.webp")
        self.assertTrue(path.parent.is_dir())

    def test_a_file_left_at_the_top_is_still_found(self):
        (self.dir / self.name()).write_bytes(b"x")
        self.assertEqual(images.path_for(self.name()), self.dir / self.name())

    def test_migration_moves_what_is_left_at_the_top_and_repeats_harmlessly(self):
        for n in ("ab12cd", "ffee00"):
            (self.dir / (n + ".webp")).write_bytes(b"x")
        (self.dir / "ab").mkdir()
        (self.dir / "ab" / "abcdef.webp").write_bytes(b"x")
        self.assertEqual(migrate_images.main(["--dry-run"]), 0)
        self.assertTrue((self.dir / "ab12cd.webp").exists(), "a dry run moves nothing")
        migrate_images.main([])
        self.assertEqual(images.path_for("ab12cd.webp"), self.dir / "ab" / "ab12cd.webp")
        self.assertEqual(images.path_for("ffee00.webp"), self.dir / "ff" / "ffee00.webp")
        self.assertEqual(sorted(n for n, _ in images.every_file()),
                         ["ab12cd.webp", "abcdef.webp", "ffee00.webp"])
        migrate_images.main([])
        self.assertEqual(len(list(images.every_file())), 3)

    def test_migration_refuses_to_move_pictures_out_of_the_cache(self):
        """The guard against the two halves disagreeing about where the cache is."""
        (self.dir / self.name()).write_bytes(b"x")
        elsewhere = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, elsewhere, True)
        with mock.patch.object(images, "path_for",
                               lambda n, write=False: elsewhere / n[:2] / n), \
                mock.patch("sys.stderr"):
            self.assertEqual(migrate_images.main([]), 2)
        self.assertTrue((self.dir / self.name()).exists(), "nothing left the cache")
        self.assertEqual(list(elsewhere.rglob("*.webp")), [])

    def test_prune_reaches_files_in_subdirectories(self):
        keep = images.path_for("ab12cd.webp", write=True)
        keep.write_bytes(b"x")
        images.path_for("ffee00.webp", write=True).write_bytes(b"x")
        with mock.patch.object(prune, "connect") as connect:
            cur = connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
            cur.fetchone.return_value = (2,)
            cur.fetchall.return_value = [("ab12cd.webp",)]
            prune.main()
        self.assertTrue(keep.exists())
        self.assertFalse((self.dir / "ff" / "ffee00.webp").exists())


class StalePages(unittest.TestCase):
    """Every page is rewritten by every build, so an old one is not a page."""

    def setUp(self):
        self.site = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.site, True)
        for rel in ("index.html", "orgs/current.html", "orgs/gone.html",
                    "img/ab/old.webp", "fonts/plex.woff2", "flags/vt.webp"):
            path = self.site / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        old = time.time() - 30 * 86400
        for rel in ("orgs/gone.html", "img/ab/old.webp", "fonts/plex.woff2", "flags/vt.webp"):
            os.utime(self.site / rel, (old, old))

    def test_finds_only_pages_no_build_has_written(self):
        found = prune.stale_pages(self.site, days=7)
        self.assertEqual([pathlib.Path(p).name for p in found], ["gone.html"])

    def test_leaves_the_pictures_fonts_and_flags_alone(self):
        found = " ".join(prune.stale_pages(self.site, days=7))
        for safe in ("old.webp", "plex.woff2", "vt.webp"):
            self.assertNotIn(safe, found)

    def test_nothing_is_stale_when_the_window_is_long(self):
        self.assertEqual(prune.stale_pages(self.site, days=90), [])


class Pictures(unittest.TestCase):
    """The space a picture will take is reserved; how it reads until then."""

    def test_the_first_pictures_on_a_page_do_not_wait_to_be_scrolled_to(self):
        items = [article(id=n, image_file=f"{n}.webp", image_w=480, image_h=320)
                 for n in range(1, cards.EAGER_IMAGES + 3)]
        html = cards.render_feed(None, items, with_related=False)
        tags = re.findall(r"<img[^>]*\d+\.webp[^>]*>", html)
        self.assertEqual(len(tags), cards.EAGER_IMAGES + 2)
        for tag in tags[:cards.EAGER_IMAGES]:
            self.assertIn('fetchpriority="high"', tag)
            self.assertNotIn("lazy", tag)
        for tag in tags[cards.EAGER_IMAGES:]:
            self.assertIn('loading="lazy"', tag)

    def test_a_skipped_house_image_does_not_use_up_an_eager_place(self):
        items = [article(id=1, image_file="house.webp", image_w=480, image_h=320),
                 article(id=2, image_file="story.webp", image_w=480, image_h=320)]
        html = cards.render_feed(None, items, with_related=False, skip_images={"house.webp"})
        self.assertNotIn("house.webp", html)
        self.assertIn('fetchpriority="high"', html)

    def test_the_reserved_box_is_tinted_rather_than_white(self):
        self.assertRegex(shell.stylesheet(), r"\.shot img\{[^}]*background:var\(--band\)")

    def test_a_picture_that_fails_to_load_takes_its_box_with_it(self):
        self.assertIn('img.closest("figure.shot")', shell.IMAGE_SCRIPT)
        self.assertIn("shot.remove()", shell.IMAGE_SCRIPT)
        self.assertIn(shell.IMAGE_SCRIPT, shell.page("Anything", "<p>body</p>"))


class Stylesheet(unittest.TestCase):
    def test_no_motion_property_applies_under_reduced_motion(self):
        css = shell.stylesheet()
        block = re.search(r"@media\(prefers-reduced-motion:no-preference\)\{(.*?)\n\n",
                          css + "\n\n", re.S).group(1)
        self.assertIn("disc-open", block)
        # Every disclosure animation lives inside the no-preference block.
        self.assertEqual(css.count("animation:"), block.count("animation:"))
        self.assertEqual(css.count("@keyframes"), block.count("@keyframes"))
        self.assertNotIn("transition", css)

    def test_marker_is_styled_and_focusable(self):
        css = shell.stylesheet()
        self.assertIn(".disc>summary{cursor:pointer;list-style:none", css)
        self.assertIn(".disc>summary:focus-visible .disc-cue", css)


class DisclosureBehaviour(unittest.TestCase):
    """What a disclosure does once it is on the page.

    All of it is the browser's own behaviour, so what these guard is that
    nothing in the markup or the stylesheet takes it away again.
    """

    def cards(self):
        """Both disclosures on one card: the publication and the byline."""
        with reporters_loaded(**{"dana reyes": reporter()}):
            return card()

    def summaries(self, html):
        return re.findall(r"<summary>(.*?)</summary>", html, re.S)

    def test_both_summaries_are_plain_summaries_the_browser_can_operate(self):
        # No role, no tabindex and no aria-expanded: a <summary> already takes
        # Tab, toggles on Enter and Space, and announces its own state. Any
        # of those attributes would override what it does natively, and an
        # aria-expanded nothing keeps in sync would go stale on first click.
        html = self.cards()
        self.assertEqual(len(self.summaries(html)), 2)
        for marker in self.summaries(html):
            for attr in ("role=", "tabindex=", "aria-expanded", "aria-controls"):
                self.assertNotIn(attr, marker)

    def test_no_interactive_element_sits_inside_a_summary(self):
        # A link inside a summary swallows the toggle in several browsers and
        # puts a second tab stop in front of the marker.
        for marker in self.summaries(self.cards()):
            self.assertNotIn("<a ", marker)
            self.assertNotIn("<button", marker)

    def test_the_summary_keeps_its_own_display(self):
        # Overriding display on a <summary> is what costs it its disclosure
        # semantics; what the marker needs of its own hangs on a span inside.
        css = shell.stylesheet()
        rule = re.search(r"\n\.disc>summary\{(.*?)\}", css, re.S).group(1)
        self.assertNotIn("display:", rule)
        self.assertIn('<span class="disc-line">', self.cards())

    def test_the_summary_line_reads_as_text_beside_the_tag_column(self):
        # Name and cue laid out together as a box — an inline-flex, say — are
        # one atomic inline box, and a box wider than what the floated tag
        # column leaves of the line goes below the float whole, carrying the
        # headline and the story down with it. They have to break like prose.
        css = shell.stylesheet()
        self.assertIsNone(re.search(r"\n\.disc-line\{", css))
        self.assertIn(".disc-cue{display:inline-block;vertical-align:baseline;"
                      "margin-left:.4rem", css)

    def test_the_marker_carries_a_visible_focus_ring(self):
        css = shell.stylesheet()
        self.assertIn(".disc>summary:focus-visible{outline:none}", css)
        self.assertIn(".disc>summary:focus-visible .disc-line{outline:3px solid", css)

    def test_the_marker_does_not_rely_on_the_browsers_own_triangle(self):
        css = shell.stylesheet()
        self.assertIn(".disc>summary{cursor:pointer;list-style:none", css)
        self.assertIn(".disc>summary::-webkit-details-marker{display:none}", css)
        # Something of its own in its place, and it points the other way once
        # the panel is open.
        self.assertIn(".disc-cue::after{content:", css)
        self.assertIn(".disc[open]>summary .disc-cue::after{border-top-color:", css)
        self.assertIn(".disc>summary:focus-visible .disc-cue", css)

    def test_the_cue_is_drawn_rather_than_written(self):
        # A caret written as a glyph joins the summary's accessible name and
        # is read out after every masthead, on top of the expanded and
        # collapsed the browser announces by itself.
        for css in (shell.stylesheet(), pages.TEXT_CSS):
            for glyph in ("▾", "▴", "▸", "▼", "▲"):
                self.assertNotIn(glyph, css)
                self.assertNotIn(f"\\{ord(glyph):04x}", css.lower())

    def test_the_panel_opens_below_the_marker_and_moves_nothing_above_it(self):
        # The panel is the last thing in the <details> and the <details> is
        # in the flow of the card, so opening one only ever grows the card
        # downwards. Nothing takes it out of flow or pins it anywhere.
        html = cards.disclosure("m", "<p>p</p>")
        self.assertTrue(html.endswith('<div class="disc-panel"><p>p</p></div></details>'))
        rule = re.search(r"\n\.disc-panel\{(.*?)\}", shell.stylesheet(), re.S).group(1)
        for out_of_flow in ("position:absolute", "position:fixed", "position:sticky",
                            "float:", "height:"):
            self.assertNotIn(out_of_flow, rule)

    def test_state_is_the_browsers_to_keep_not_the_builds(self):
        # Nothing is rendered open, and nothing on the page closes one: a
        # panel a reader opened is still open when they scroll back to it.
        html = self.cards()
        self.assertNotIn("<details class=\"disc\" open", html)
        self.assertNotIn(" open>", html)
        self.assertNotIn("<script", html)

    def test_the_menu_script_cannot_reach_a_card_disclosure(self):
        # The one script that closes a <details> on click is scoped to the
        # menu. If it ever widened to "details[open]" every open profile on
        # the page would slam shut on the next click.
        selectors = re.findall(r'querySelector(?:All)?\("(.*?)"\)', shell.MENU_SCRIPT)
        self.assertTrue(selectors)
        for selector in selectors:
            self.assertTrue(selector.startswith("details.menu"), selector)


class PlainTextEdition(unittest.TestCase):
    def test_carries_the_profile_as_text_and_no_marker(self):
        html = pages.render_text_item(article())
        self.assertIn("<dt>About The Ledger</dt>", html)
        self.assertIn("worker-owned newsroom covering the county", html)
        self.assertNotIn("disc-cue", html)

    def test_omits_the_row_when_there_is_no_usable_about_text(self):
        html = pages.render_text_item(article(about_text=None))
        self.assertNotIn("<dt>About", html)
        self.assertNotIn("disc-cue", html)

    def test_one_marker_an_item_and_it_opens_onto_something(self):
        # The full edition's two profile markers do not follow the profile
        # text into this edition: there is the Details block and nothing else.
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = pages.render_text_item(article())
        self.assertEqual(html.count("<summary>"), 1)
        self.assertEqual(html.count("<details>"), 1)
        panel = re.search(r"</summary>(.*?)</details>", html, re.S).group(1)
        self.assertIn("<dt>About The Ledger</dt>", panel)
        self.assertIn("<dt>About Dana Reyes</dt>", panel)

    def test_the_barest_item_still_opens_onto_something(self):
        # Strip an item back to nothing disclosable and the Details block is
        # still not empty — an inert marker would be worse than no marker.
        bare = article(author=None, summary=None, subject=None, about_text=None,
                       coverage=None, beat=None, city=None, image_alt=None,
                       features=[], model="", published_at=None)
        html = pages.render_text_item(bare)
        self.assertEqual(html.count("<summary>"), 1)
        panel = re.search(r"</summary>(.*?)</details>", html, re.S).group(1)
        self.assertIn("<dd>", panel)

    def test_the_details_marker_is_the_editions_own(self):
        self.assertIn("summary::-webkit-details-marker{display:none}", pages.TEXT_CSS)
        self.assertIn("summary::after{content:", pages.TEXT_CSS)
        self.assertIn("details[open] summary::after{border-top-color:", pages.TEXT_CSS)
        # Still a list-item, which is what its announcement depends on.
        rule = re.search(r"\nsummary\{(.*?)\}", pages.TEXT_CSS, re.S).group(1)
        self.assertNotIn("display:", rule)
        self.assertIn(":focus-visible{outline:", pages.TEXT_CSS)


class ResolvingAByline(unittest.TestCase):
    def test_a_plain_name_is_a_person(self):
        for byline in ("Dana Reyes", "By Dana Reyes", "by: DANA REYES",
                       "Dana Reyes (The Ledger)", "<b>Dana Reyes</b>"):
            self.assertEqual(rp.reporter_key(byline), "dana reyes", byline)

    def test_initials_capitals_and_accents_fold_to_one_identity(self):
        self.assertEqual(rp.reporter_key("J. R. Okonkwo"),
                         rp.reporter_key("J R Okonkwo"))
        self.assertEqual(rp.reporter_key("JOSÉ GARCÍA"),
                         rp.reporter_key("José García"))

    def test_a_desk_a_wire_or_a_crowd_is_not_a_person(self):
        for byline in ("", None, "   ", "Staff", "Staff Report", "Newsroom",
                       "Editorial Board", "The Associated Press", "Guest Columnist",
                       "Dana Reyes and Kim Lee", "Dana Reyes, Kim Lee",
                       "Dana Reyes | The Ledger", "news@ledger.example",
                       "Dana", "A statement released on Tuesday by the county"):
            self.assertEqual(rp.reporter_name(byline), "", repr(byline))
            self.assertEqual(rp.reporter_key(byline), "", repr(byline))

    def test_prolificacy_says_it_one_way(self):
        self.assertEqual(rp.prolificacy(1), "One story on this site.")
        self.assertEqual(rp.prolificacy(3), "3 stories on this site.")
        self.assertIn("a regular byline", rp.prolificacy(7))
        self.assertIn("most prolific", rp.prolificacy(40))


class ReporterPanel(unittest.TestCase):
    def test_a_resolved_byline_gets_a_marker(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = card()
        self.assertIn('<details class="disc byline">', html)
        marker = re.findall(r"<summary>(.*?)</summary>", html, re.S)[-1]
        self.assertIn("By <strong>Dana Reyes</strong>", marker)
        self.assertIn('<span class="disc-cue">Profile</span>', marker)

    def test_an_unresolved_byline_gets_no_marker_at_all(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = card(author="Staff Report")
        self.assertIn('<p class="byline">By Staff Report</p>', html)
        self.assertNotIn('class="disc byline"', html)
        # Not a disabled marker and not an empty panel either.
        self.assertEqual(html.count("disc-cue"), 1)

    def test_no_marker_when_the_build_resolved_nobody(self):
        html = card()
        self.assertIn('<p class="byline">By Dana Reyes</p>', html)
        self.assertNotIn('class="disc byline"', html)

    def test_panel_shows_count_newsrooms_span_and_recent_headlines(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            panel = cards.reporter_panel(article())
        self.assertIn(rp.prolificacy(7), panel)
        self.assertIn("Publishes with The Ledger and VTDigger.", panel)
        self.assertIn("Their work here runs from June 2025 to June 2026.", panel)
        self.assertIn(">County buys the old mill</a>", panel)
        self.assertIn(">Council splits on the budget</a>", panel)

    def test_marker_uses_the_resolved_name_not_the_raw_byline(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = card(author="By Dana Reyes")
        self.assertIn("By <strong>Dana Reyes</strong>", html)
        self.assertNotIn("By By", html)

    def test_a_long_list_of_newsrooms_is_capped(self):
        rooms = [f"Paper {n}" for n in range(9)]
        with reporters_loaded(**{"dana reyes": reporter(newsrooms=rooms)}):
            panel = cards.reporter_panel(article())
        self.assertIn("Publishes with Paper 0, Paper 1, Paper 2, Paper 3 "
                      "and 5 other newsrooms.", panel)

    def test_the_panel_links_nowhere_the_build_does_not_write(self):
        # The build writes orgs/ and no reporters/ — a lozenge pointing at
        # reporters/<slug>.html would be a 404 on every resolved byline on
        # the site. Every link in the panel is a story on its publisher.
        with reporters_loaded(**{"dana reyes": reporter()}):
            panel = cards.reporter_panel(article())
        self.assertNotIn("reporters/", panel)
        self.assertEqual(re.findall(r'href="(.*?)"', panel),
                         ["https://ledger.example/story",
                          "https://ledger.example/budget"])

    def test_prolificacy_is_the_shared_string_verbatim(self):
        for n in (1, 3, 7, 40):
            with reporters_loaded(**{"dana reyes": reporter(n_stories=n)}):
                panel = cards.reporter_panel(article())
                text = pages.render_text_item(article())
            self.assertIn(rp.prolificacy(n), panel)
            self.assertIn(rp.prolificacy(n), text)

    def test_a_single_month_of_work_reads_as_one(self):
        june = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(cards.reporter_span(june, june), "All of it from June 2026.")

    def test_disclosure_adds_no_script(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            self.assertNotIn("<script", card())


class ReporterPanelQuery(unittest.TestCase):
    ROWS = [
        ("By Dana Reyes", 2, datetime(2026, 1, 1, tzinfo=timezone.utc),
         datetime(2026, 3, 1, tzinfo=timezone.utc), ["VTDigger"],
         [{"title": "Older", "url": "u1", "ts": 1.0}]),
        ("Dana Reyes", 5, datetime(2025, 6, 1, tzinfo=timezone.utc),
         datetime(2026, 6, 1, tzinfo=timezone.utc), ["The Ledger"],
         [{"title": "Newest", "url": "u2", "ts": 9.0}]),
        ("Staff Report", 91, datetime(2025, 1, 1, tzinfo=timezone.utc),
         datetime(2026, 6, 1, tzinfo=timezone.utc), ["The Ledger"], None),
    ]

    def test_one_query_for_the_whole_build(self):
        cur = FakeCursor(self.ROWS)
        cards.load_reporter_panels(cur)
        self.assertEqual(len(cur.queries), 1)

    def test_the_query_hands_back_each_spelling_of_a_byline_separately(self):
        # Grouping on the case-folded byline would collapse "Dana Reyes" and
        # "DANA REYES" into a single row inside the database, and whichever
        # of them sorted first would be the name on the card — the merge
        # below never gets to prefer the spelling that is not shouted.
        cur = FakeCursor(self.ROWS)
        cards.load_reporter_panels(cur)
        sql = cur.queries[0][0]
        self.assertIn("GROUP BY author", sql)
        self.assertIn("PARTITION BY author", sql)
        self.assertNotIn("lower(", sql)

    def test_one_person_written_two_ways_is_one_reporter(self):
        panels = cards.load_reporter_panels(FakeCursor(self.ROWS))
        self.assertEqual(list(panels), ["dana reyes"])
        who = panels["dana reyes"]
        self.assertEqual(who["n_stories"], 7)
        self.assertEqual(who["newsrooms"], ["The Ledger", "VTDigger"])
        self.assertEqual(who["first_at"].year, 2025)
        self.assertEqual(who["last_at"].month, 6)
        self.assertEqual([r["title"] for r in who["recent"]], ["Newest", "Older"])

    def test_one_spelling_of_the_name_whatever_order_the_rows_arrive_in(self):
        rows = [
            ("DANA REYES", 1, datetime(2026, 1, 1, tzinfo=timezone.utc),
             datetime(2026, 1, 1, tzinfo=timezone.utc), ["VTDigger"], None),
            ("Dana Reyes", 1, datetime(2026, 2, 1, tzinfo=timezone.utc),
             datetime(2026, 2, 1, tzinfo=timezone.utc), ["The Ledger"], None),
        ]
        for order in (rows, rows[::-1]):
            panels = cards.load_reporter_panels(FakeCursor(order))
            self.assertEqual(panels["dana reyes"]["name"], "Dana Reyes")

    def test_headlines_are_capped(self):
        rows = [("Dana Reyes", 9, datetime(2026, 1, 1, tzinfo=timezone.utc),
                 datetime(2026, 6, 1, tzinfo=timezone.utc), ["The Ledger"],
                 [{"title": f"H{n}", "url": f"u{n}", "ts": float(n)} for n in range(6)])]
        who = cards.load_reporter_panels(FakeCursor(rows), headlines=2)["dana reyes"]
        self.assertEqual([r["title"] for r in who["recent"]], ["H5", "H4"])


class SearchService(unittest.TestCase):
    """The live service renders the same cards, so it needs the same data.

    `searchd` is a separate long-running process: nothing the build leaves
    in memory reaches it, which is why it already looks the menu up for
    itself at startup. The reporter profiles are the same kind of thing.
    """

    def test_startup_fills_the_reporter_profiles_it_renders(self):
        cur = FakeCursor(ReporterPanelQuery.ROWS)
        self.addCleanup(cards.REPORTERS.clear)
        with mock.patch.object(searchd, "connect", lambda: FakeConnection(cur)):
            searchd.load_reporters()
        self.assertEqual(cards.REPORTERS["dana reyes"]["name"], "Dana Reyes")
        self.assertEqual(len(cur.queries), 1)

    def test_a_result_card_carries_the_byline_disclosure(self):
        cur = FakeCursor(ReporterPanelQuery.ROWS)
        self.addCleanup(cards.REPORTERS.clear)
        with mock.patch.object(searchd, "connect", lambda: FakeConnection(cur)):
            searchd.load_reporters()
        # A result card is rendered exactly the way searchd renders one.
        html = cards.render_feed_item(None, article(), with_related=False)
        self.assertIn('<details class="disc byline">', html)
        self.assertIn("By <strong>Dana Reyes</strong>", html)

    def test_the_service_selects_the_about_text_the_publication_panel_needs(self):
        self.assertIn("o.about_text", searchd.SELECT_COLS)
        self.assertIn("about_text", searchd.COLS)


class ReporterInPlainText(unittest.TestCase):
    def test_carries_the_profile_as_text_and_no_marker(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = pages.render_text_item(article())
        self.assertIn("<dt>Reported by</dt><dd>Dana Reyes</dd>", html)
        self.assertIn("<dt>About Dana Reyes</dt>", html)
        self.assertIn("Publishes with The Ledger and VTDigger.", html)
        self.assertNotIn("disc-cue", html)
        self.assertNotIn("reporters/", html)

    def test_an_unresolved_byline_says_nothing_extra(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = pages.render_text_item(article(author="Staff Report"))
        self.assertIn("<dt>Reported by</dt><dd>Staff Report</dd>", html)
        self.assertNotIn("<dt>About Dana", html)
        self.assertNotIn("stories on this site", html)


class SearchCursor(FakeCursor):
    """A cursor for the two queries `run_search` runs: the page, then the count."""

    def __init__(self, rows, total):
        super().__init__(rows)
        self.total = total

    def fetchone(self):
        return (self.total,)


def result_row(title, slug, name):
    """One row shaped the way `searchd.SELECT_COLS` returns them."""
    row = dict.fromkeys(searchd.COLS)
    row.update(id=abs(hash(slug + title)) % 9999, url=f"https://{slug}.example/story",
               title=title, summary="Words.",
               published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
               org_name=name, slug=slug, org_url=f"https://{slug}.example",
               state="CA", city="Fresno", coverage_type="local",
               timezone="America/Los_Angeles", features=[], language="English",
               org_feed=f"https://{slug}.example/feed")
    return tuple(row[col] for col in searchd.COLS)


REPRINT = "Newsom vetoed a data center water bill last year. Will he sign it this time?"


class SearchFoldsReprints(unittest.TestCase):
    """A syndicated story is one result, not one per newsroom that ran it.

    Search was the last surface still showing every copy: the feed, tag and
    subject pages have always folded them, so a search for a wire story came
    back as the same headline eight times over.
    """

    ROWS = [result_row(REPRINT, "calmatters", "CalMatters"),
            result_row(REPRINT, "sjspotlight", "San José Spotlight"),
            result_row(REPRINT, "benitolink", "BenitoLink"),
            result_row("City of Fresno weighs data center ban", "fresnoland", "Fresnoland")]

    def search(self):
        return searchd.run_search(SearchCursor(self.ROWS, 453), "data centers",
                                  (), "", "", None)

    def test_the_copies_fold_into_one_result(self):
        rows, _total = self.search()
        self.assertEqual([r["title"] for r in rows],
                         [REPRINT, "City of Fresno weighs data center ban"])

    def test_the_newsrooms_that_ran_it_are_still_named(self):
        rows, _total = self.search()
        self.assertEqual([o["org_name"] for o in rows[0]["_also"]],
                         ["San José Spotlight", "BenitoLink"])
        html = cards.render_feed_item(None, rows[0], with_related=False)
        self.assertIn("Also in", html)
        self.assertIn("BenitoLink", html)

    def test_a_distinct_story_keeps_its_own_card(self):
        rows, _total = self.search()
        self.assertEqual(rows[1]["_also"], [])

    def test_the_total_stays_the_unfolded_count(self):
        # Folding a whole result set to count it would cost a second pass
        # over every match; the pager and the count are about the query.
        _rows, total = self.search()
        self.assertEqual(total, 453)


if __name__ == "__main__":
    unittest.main()


class ShareCard(unittest.TestCase):
    """The picture a link to this site unfurls into."""

    def test_every_page_carries_the_card(self):
        html = shell.page("A page", "<p>x</p>")
        self.assertIn('<meta property="og:image" '
                      f'content="{config.SITE_URL}/{config.SHARE_IMAGE}">', html)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', html)
        self.assertIn('<meta property="og:title" content="A page">', html)
        # An absolute URL: a scraper is not on the site and resolves nothing.
        self.assertNotIn(f'og:image" content="{config.SHARE_IMAGE}"', html)

    def test_the_card_is_the_size_the_pages_promise(self):
        width, height = config.SHARE_IMAGE_SIZE
        self.assertIn(f'<meta property="og:image:width" content="{width}">',
                      shell.page("A page", "<p>x</p>"))
        with Image.open(config.ASSETS_DIR / config.SHARE_IMAGE) as card:
            self.assertEqual(card.format, "PNG")  # no platform renders SVG
            self.assertEqual(card.size, (width, height))

    def test_a_page_with_nothing_of_its_own_to_say_says_what_the_site_is(self):
        html = shell.page("A page", "<p>x</p>")
        for attr in ('name="description"', 'property="og:description"'):
            self.assertIn(f'<meta {attr} content="{config.SITE_DESCRIPTION}">', html)
        own = shell.page("A page", "<p>x</p>", description="Its own words.")
        self.assertIn('<meta name="description" content="Its own words.">', own)
        self.assertIn('<meta property="og:description" content="Its own words.">', own)

    def test_no_page_claims_to_be_the_canonical_one(self):
        # The shell does not know the path it is being written to, and an
        # og:url that is right on one page and wrong on the rest is worse
        # than leaving the scraper with the URL it fetched.
        self.assertNotIn("og:url", shell.page("A page", "<p>x</p>"))

    def test_the_card_is_drawn_from_the_catalog(self):
        html = share_card.card_html()
        self.assertIn(config.SITE_DESCRIPTION, html)
        self.assertIn("givemesomegood.news", html)
        # The masthead's letterforms are paths, so the card needs no font
        # for them, and there is a dot for every town in the catalog.
        self.assertIn('class="wordmark"', html)
        self.assertGreater(html.count("<circle"), 300)

    def test_the_card_is_a_copied_asset_not_a_page_the_build_draws(self):
        # The fifteen-minute build must not have to run a browser.
        source = (config.ROOT / "givemesomegoodnews" / "build_site.py").read_text()
        self.assertIn("config.ASSETS_DIR / config.SHARE_IMAGE", source)
        for importing in ("import share_card", "from .share_card"):
            self.assertNotIn(importing, source)
