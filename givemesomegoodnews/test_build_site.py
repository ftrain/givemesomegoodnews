"""Tests for the inline disclosure on a feed card.

Run with `python3 -m unittest givemesomegoodnews.test_build_site`. Nothing
here touches the database: `render_feed_item` is given `with_related=False`,
which is the same thing `searchd` does, so no cursor is ever used.
"""

import re
import unittest
from datetime import datetime, timezone

from . import build_site as bs


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
    return bs.render_feed_item(None, article(**over), with_related=False)


class DisclosureHelper(unittest.TestCase):
    def test_wraps_marker_and_panel_in_details(self):
        html = bs.disclosure("<strong>X</strong>", "<p>hello</p>")
        self.assertIn('<details class="disc">', html)
        self.assertIn("<summary><strong>X</strong>", html)
        self.assertIn('<div class="disc-panel"><p>hello</p></div>', html)

    def test_no_marker_when_there_is_nothing_to_disclose(self):
        self.assertEqual(bs.disclosure("<strong>X</strong>", ""), "")

    def test_extra_class_joins_the_base_class(self):
        html = bs.disclosure("m", "<p>p</p>", "source")
        self.assertIn('<details class="disc source">', html)


class PublicationPanel(unittest.TestCase):
    def test_card_carries_a_marker_beside_the_publication_name(self):
        html = card()
        self.assertIn('<details class="disc source">', html)
        marker = re.search(r"<summary>(.*?)</summary>", html, re.S).group(1)
        self.assertIn("<strong>The Ledger</strong>", marker)
        self.assertIn('<span class="disc-cue">Profile</span>', marker)

    def test_panel_shows_description_coverage_tags_and_three_links(self):
        html = card()
        panel = re.search(r'<div class="disc-panel">(.*?)</div>', html, re.S).group(1)
        self.assertIn("worker-owned newsroom covering the county", panel)
        self.assertIn("Covers Rutland County.", panel)
        for tag in ("Worker-owned", "Reader-funded", "INN member"):
            self.assertIn(f">{tag}</a>", panel)
        self.assertIn('href="https://ledger.example/">Their site</a>', panel)
        self.assertIn('href="orgs/the-ledger.html">Newsroom page</a>', panel)
        self.assertIn('href="https://ledger.example/donate"', panel)

    def test_panel_shows_the_whole_feature_set_uncapped(self):
        many = [f"Tag{n}" for n in range(9)]
        panel = bs.org_profile_panel(article(features=many, model=""))
        for tag in many:
            self.assertIn(f">{tag}</a>", panel)

    def test_unusable_about_text_still_gets_a_panel(self):
        for about in (None, "", "Too short to be anybody's About page."):
            panel = bs.org_profile_panel(article(about_text=about))
            self.assertNotIn("<blockquote>", panel)
            self.assertIn("Covers Rutland County.", panel)
            self.assertIn(">Worker-owned</a>", panel)
            self.assertIn("Their site</a>", panel)
            self.assertIn("Newsroom page</a>", panel)

    def test_quote_is_an_excerpt_not_the_whole_about_page(self):
        panel = bs.org_profile_panel(article())
        quote = re.search(r"<blockquote><p>(.*?)</p></blockquote>", panel, re.S).group(1)
        self.assertLessEqual(len(quote), 430)
        self.assertNotIn("weekly print edition", quote)


class AboutOpening(unittest.TestCase):
    def test_skips_a_heading_or_stray_line_to_reach_the_description(self):
        text = "About The Ledger\n\nPublished: June 30, 2025\n\n" + ABOUT
        self.assertTrue(bs.about_opening(text).startswith("The Ledger is a worker-owned"))

    def test_truncates_a_long_paragraph(self):
        got = bs.about_opening("word " * 300)
        self.assertLessEqual(len(got), 430)
        self.assertTrue(got.endswith("[…]"))

    def test_nothing_from_an_unusable_about_text(self):
        for text in (None, "", "Short.", "About Us\n\nRead more"):
            self.assertEqual(bs.about_opening(text), "")

    def test_prefix_reaches_the_org_page_from_a_subdirectory(self):
        panel = bs.org_profile_panel(article(), prefix="../")
        self.assertIn('href="../orgs/the-ledger.html"', panel)

    def test_onepage_stays_self_contained(self):
        panel = bs.org_profile_panel(article(), mode="onepage")
        self.assertNotIn("orgs/the-ledger.html", panel)
        self.assertIn('href="https://ledger.example/">Their site</a>', panel)

    def test_disclosure_adds_no_script(self):
        self.assertNotIn("<script", card())

    def test_disclosure_opens_after_the_places_row_not_before_it(self):
        html = card()
        self.assertLess(html.index('class="places"'), html.index('class="disc source"'))
        self.assertLess(html.index('class="disc source"'), html.index("<h2>"))


class Stylesheet(unittest.TestCase):
    def test_no_motion_property_applies_under_reduced_motion(self):
        css = bs.stylesheet()
        block = re.search(r"@media\(prefers-reduced-motion:no-preference\)\{(.*?)\n\n",
                          css + "\n\n", re.S).group(1)
        self.assertIn("disc-open", block)
        # Every disclosure animation lives inside the no-preference block.
        self.assertEqual(css.count("animation:"), block.count("animation:"))
        self.assertEqual(css.count("@keyframes"), block.count("@keyframes"))
        self.assertNotIn("transition", css)

    def test_marker_is_styled_and_focusable(self):
        css = bs.stylesheet()
        self.assertIn(".disc>summary{cursor:pointer;list-style:none", css)
        self.assertIn(".disc>summary:focus-visible .disc-cue", css)


class PlainTextEdition(unittest.TestCase):
    def test_carries_the_profile_as_text_and_no_marker(self):
        html = bs.render_text_item(article())
        self.assertIn("<dt>About The Ledger</dt>", html)
        self.assertIn("worker-owned newsroom covering the county", html)
        self.assertNotIn("disc-cue", html)

    def test_omits_the_row_when_there_is_no_usable_about_text(self):
        html = bs.render_text_item(article(about_text=None))
        self.assertNotIn("<dt>About", html)
        self.assertNotIn("disc-cue", html)


if __name__ == "__main__":
    unittest.main()
