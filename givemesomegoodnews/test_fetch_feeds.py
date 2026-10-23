"""The crawler's paging: how --backfill walks a feed back to a date."""

import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from . import fetch_feeds


def _feed(links, days, next_href=None):
    """A parsed feed as feedparser would hand it over: entries with links and
    published times, and optionally an RFC 5005 next link."""
    entries = []
    for link, day in zip(links, days):
        entries.append({"link": link, "published_parsed": (2026, 9, day, 12, 0, 0, 0, 0, 0)})
    feed = {"links": [{"rel": "next", "href": next_href}] if next_href else []}
    return mock.Mock(entries=entries, feed=feed)


class NextPage(unittest.TestCase):
    def test_wordpress_paged_parameter(self):
        page = _feed(["a"], [20])
        self.assertEqual(fetch_feeds._next_page("https://x.org/feed/", page, 2),
                         "https://x.org/feed/?paged=2")

    def test_replaces_an_existing_page_number(self):
        page = _feed(["a"], [20])
        self.assertEqual(fetch_feeds._next_page("https://x.org/feed/?paged=2&cat=1", page, 3),
                         "https://x.org/feed/?cat=1&paged=3")

    def test_prefers_the_feeds_own_next_link(self):
        page = _feed(["a"], [20], next_href="/feed/?page=9")
        self.assertEqual(fetch_feeds._next_page("https://x.org/feed/", page, 2),
                         "https://x.org/feed/?page=9")


class PageBack(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stops_once_the_date_is_reached(self):
        pages = {
            "https://x.org/feed/?paged=2": _feed(["c", "d"], [22, 21]),
            "https://x.org/feed/?paged=3": _feed(["e", "f"], [16, 15]),
            "https://x.org/feed/?paged=4": _feed(["g"], [10]),
        }
        front = _feed(["a", "b"], [25, 24])
        with mock.patch.object(fetch_feeds, "_fetch_feed", side_effect=pages.get) as fetched:
            entries = fetch_feeds._page_back("https://x.org/feed/", front,
                                             datetime(2026, 9, 17, tzinfo=timezone.utc))
        self.assertEqual([e["link"] for e in entries], list("abcdef"))
        # Page 3 is read because page 2 was still newer than the date; page 3
        # reaches past it, so page 4 is never asked for.
        self.assertEqual(fetched.call_count, 2)

    def test_stops_when_the_site_ignores_the_page_number(self):
        front = _feed(["a", "b"], [25, 24])
        with mock.patch.object(fetch_feeds, "_fetch_feed", return_value=front) as fetched:
            entries = fetch_feeds._page_back("https://x.org/feed/", front,
                                             datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual([e["link"] for e in entries], ["a", "b"])
        self.assertEqual(fetched.call_count, 1)

    def test_stops_where_the_feed_ends(self):
        front = _feed(["a"], [25])
        with mock.patch.object(fetch_feeds, "_fetch_feed", return_value=None):
            entries = fetch_feeds._page_back("https://x.org/feed/", front,
                                             datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
