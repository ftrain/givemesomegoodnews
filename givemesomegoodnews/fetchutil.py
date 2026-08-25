"""Polite HTTP fetching and feed autodiscovery."""

import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests

from . import config

_session = None


def session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml,*/*",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )
    return _session


def get(url, retries=1, timeout=None):
    """GET a URL; returns a Response or raises the last error."""
    last = None
    for attempt in range(retries + 1):
        try:
            resp = session().get(url, timeout=timeout or config.FETCH_TIMEOUT, allow_redirects=True)
            if resp.status_code >= 500 and attempt < retries:
                time.sleep(2)
                continue
            return resp
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(2)
    raise last


_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([a-zA-Z-]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""")
_A_ABOUT_RE = re.compile(
    r"""<a\b[^>]*href\s*=\s*["']([^"']*about[^"']*)["']""", re.IGNORECASE
)

# Common feed locations, tried in order when autodiscovery fails. The last
# one is the TownNews/BLOX pattern used by many small legacy papers.
FEED_CANDIDATE_PATHS = [
    "/feed/",
    "/feed",
    "/rss/",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/feeds/main/",
    "/arc/outboundfeeds/rss/?outputType=xml",
    "/search/?f=rss&t=article&l=30&s=start_time&sd=desc",
]


def _tag_attrs(tag_html):
    attrs = {}
    for m in _ATTR_RE.finditer(tag_html):
        val = m.group(3) or m.group(4) or m.group(5) or ""
        attrs[m.group(1).lower()] = val
    return attrs


def feed_links_in_html(base_url, html):
    """Feed URLs advertised via <link rel="alternate"> in a page."""
    found = []
    for tag in _LINK_TAG_RE.findall(html):
        attrs = _tag_attrs(tag)
        rel = attrs.get("rel", "").lower()
        typ = attrs.get("type", "").lower()
        href = attrs.get("href")
        if not href or "alternate" not in rel:
            continue
        if "rss" in typ or "atom" in typ:
            url = urljoin(base_url, href)
            # Skip per-post comment feeds.
            if "comments" not in url:
                found.append(url)
    return found


def about_links_in_html(base_url, html):
    """Hrefs containing 'about', for locating an About page from a homepage."""
    seen, found = set(), []
    for href in _A_ABOUT_RE.findall(html):
        url = urljoin(base_url, href)
        if url not in seen and urlsplit(url).netloc.endswith(urlsplit(base_url).netloc.removeprefix("www.")):
            seen.add(url)
            found.append(url)
    return found


def looks_like_feed(body_bytes):
    head = body_bytes[:1000].lstrip().lower()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head or b"<rdf" in head


_DROP_QUERY_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "mc_cid", "mc_eid", "ref"}


def canonical_url(url):
    """Strip tracking params and fragments so the same story dedupes."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _DROP_QUERY_KEYS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
