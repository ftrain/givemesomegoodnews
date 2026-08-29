"""Tests for the inline disclosure on a feed card.

Run with `python3 -m unittest givemesomegoodnews.test_build_site`. Nothing
here touches the database: `render_feed_item` is given `with_related=False`,
which is the same thing `searchd` does, so no cursor is ever used.
"""

import contextlib
import re
import unittest
from datetime import datetime, timezone

from . import build_site as bs
from . import reporters as rp


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


def reporter(**over):
    who = {
        "name": "Dana Reyes", "slug": "dana-reyes", "n_stories": 7,
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
    bs.REPORTERS.clear()
    bs.REPORTERS.update(by_key)
    try:
        yield
    finally:
        bs.REPORTERS.clear()


class FakeCursor:
    """Enough of a cursor to count how many queries a build costs."""

    def __init__(self, rows):
        self.rows, self.queries = rows, []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return self.rows


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


class ResolvingAByline(unittest.TestCase):
    def test_a_plain_name_is_a_person(self):
        for byline in ("Dana Reyes", "By Dana Reyes", "by: DANA REYES",
                       "Dana Reyes (The Ledger)", "<b>Dana Reyes</b>"):
            self.assertEqual(rp.reporter_key(byline), "dana reyes", byline)

    def test_initials_and_accents_fold_to_one_identity(self):
        self.assertEqual(rp.reporter_key("J. R. Okonkwo"),
                         rp.reporter_key("J R Okonkwo"))
        self.assertEqual(rp.reporter_slug("José García"), "jose-garcia")

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
            panel = bs.reporter_panel(article())
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
            panel = bs.reporter_panel(article())
        self.assertIn("Publishes with Paper 0, Paper 1, Paper 2, Paper 3 "
                      "and 5 other newsrooms.", panel)

    def test_panel_links_to_the_reporters_page(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            self.assertIn('href="reporters/dana-reyes.html"',
                          bs.reporter_panel(article()))
            self.assertIn('href="../reporters/dana-reyes.html"',
                          bs.reporter_panel(article(), prefix="../"))
            self.assertNotIn("reporters/", bs.reporter_panel(article(), mode="onepage"))

    def test_prolificacy_is_the_shared_string_verbatim(self):
        for n in (1, 3, 7, 40):
            with reporters_loaded(**{"dana reyes": reporter(n_stories=n)}):
                panel = bs.reporter_panel(article())
                text = bs.render_text_item(article())
            self.assertIn(rp.prolificacy(n), panel)
            self.assertIn(rp.prolificacy(n), text)

    def test_a_single_month_of_work_reads_as_one(self):
        june = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(bs.reporter_span(june, june), "All of it from June 2026.")

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
        bs.load_reporter_panels(cur)
        self.assertEqual(len(cur.queries), 1)

    def test_one_person_written_two_ways_is_one_reporter(self):
        panels = bs.load_reporter_panels(FakeCursor(self.ROWS))
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
            panels = bs.load_reporter_panels(FakeCursor(order))
            self.assertEqual(panels["dana reyes"]["name"], "Dana Reyes")

    def test_headlines_are_capped(self):
        rows = [("Dana Reyes", 9, datetime(2026, 1, 1, tzinfo=timezone.utc),
                 datetime(2026, 6, 1, tzinfo=timezone.utc), ["The Ledger"],
                 [{"title": f"H{n}", "url": f"u{n}", "ts": float(n)} for n in range(6)])]
        who = bs.load_reporter_panels(FakeCursor(rows), headlines=2)["dana reyes"]
        self.assertEqual([r["title"] for r in who["recent"]], ["H5", "H4"])


class ReporterInPlainText(unittest.TestCase):
    def test_carries_the_profile_as_text_and_no_marker(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = bs.render_text_item(article())
        self.assertIn("<dt>Reported by</dt><dd>Dana Reyes</dd>", html)
        self.assertIn("<dt>About Dana Reyes</dt>", html)
        self.assertIn("Publishes with The Ledger and VTDigger.", html)
        self.assertNotIn("disc-cue", html)
        self.assertNotIn("reporters/", html)

    def test_an_unresolved_byline_says_nothing_extra(self):
        with reporters_loaded(**{"dana reyes": reporter()}):
            html = bs.render_text_item(article(author="Staff Report"))
        self.assertIn("<dt>Reported by</dt><dd>Staff Report</dd>", html)
        self.assertNotIn("<dt>About Dana", html)
        self.assertNotIn("stories on this site", html)


if __name__ == "__main__":
    unittest.main()
