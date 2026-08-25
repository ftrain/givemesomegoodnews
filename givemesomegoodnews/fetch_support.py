"""Find where each newsroom takes money, so every feed item can link to it.

The point of the site is to send readers back to these newsrooms, and the
most useful link is the one that keeps them running. This crawls each org's
homepage once, scores its outbound links, and stores the best "give them
money" destination on the org row.

Nonprofits get a Donate link where one exists; reader-funded co-ops and
family papers get Subscribe or a membership page. Hand-set `support_url`
and `support_label` in data/orgs.yaml override whatever is found here.
"""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from . import config
from .db import connect, log_fetch
from .fetchutil import get

_A_RE = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r"""href\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")

# (pattern, points, label). Checked against both the href and the link text.
# Donating outranks subscribing: for a nonprofit it is the real ask.
RULES = [
    (r"\bdonate\b|\bdonation\b|/give\b|\bgiving\b", 10, "Donate"),
    (r"support-?us|/support\b|\bsupport-our\b", 9, "Donate"),
    (r"\bmembership\b|\bbecome-a-member\b|\bmember\b", 8, "Become a member"),
    (r"\bcontribute\b|\bfund\b", 7, "Donate"),
    (r"\bsubscribe\b|\bsubscription\b", 6, "Subscribe"),
    (r"\bjoin\b", 5, "Become a member"),
]

# A newsletter signup is not financial support, and neither is a podcast feed.
NEGATIVE = re.compile(
    r"newsletter|rss|feed|podcast|instagram|twitter|facebook|bluesky|mastodon|"
    r"youtube|tiktok|linkedin|login|sign-?in|account|unsubscribe",
    re.IGNORECASE,
)

# Common payment processors — a strong signal the link really takes money.
PROCESSORS = re.compile(
    r"donorbox|givebutter|classy\.org|fundjournalism|networkforgood|every\.org|"
    r"paypal|stripe|substack\.com/subscribe|memberful|steadyhq|patreon|funraise|"
    r"givelively|actblue|kindful|neonemails|neoncrm",
    re.IGNORECASE,
)


def _anchors(base_url, html):
    """Yield (absolute_url, link_text) for every anchor in the page."""
    for attrs, inner in _A_RE.findall(html or ""):
        m = _HREF_RE.search(attrs)
        if not m:
            continue
        href = (m.group(2) or m.group(3) or m.group(4) or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = _TAG_RE.sub(" ", inner)
        text = re.sub(r"\s+", " ", text).strip()[:120]
        yield urljoin(base_url, href), text


def score_link(url, text):
    """Score one candidate. Returns (points, label) or (0, None)."""
    haystack = f"{url} {text}".lower()
    if NEGATIVE.search(haystack):
        # ...unless it is unmistakably a payment page despite the word.
        if not PROCESSORS.search(url):
            return 0, None
    best, label = 0, None
    for pattern, points, lbl in RULES:
        if re.search(pattern, haystack, re.IGNORECASE):
            if points > best:
                best, label = points, lbl
    if not best:
        return 0, None
    if PROCESSORS.search(url):
        best += 4
    # A short, on-the-nose link text ("Donate") beats a sentence containing it.
    if text and len(text) <= 20:
        best += 1
    return best, label


def preferred_label(model, label):
    """Match the ask to how the newsroom is funded."""
    model = (model or "").lower()
    if label == "Donate" and ("cooperative" in model or "for-profit" in model or "family" in model):
        return "Subscribe" if "subscribe" in model else label
    return label


def discover(org):
    """Crawl one org's homepage for its best support link."""
    try:
        resp = get(org["url"], retries=1)
        if resp.status_code != 200:
            return org, None, None, "homepage %s" % resp.status_code
        html = resp.text
    except Exception as e:
        return org, None, None, f"fetch failed: {type(e).__name__}"

    home_host = urlparse(org["url"]).netloc.lower().removeprefix("www.")
    best = (0, None, None)
    for url, text in _anchors(org["url"], html):
        points, label = score_link(url, text)
        if not points:
            continue
        host = urlparse(url).netloc.lower().removeprefix("www.")
        # Off-site links are fine (processors live elsewhere), but an
        # unrelated domain with no processor signature is probably an ad.
        if host != home_host and not PROCESSORS.search(url):
            points -= 3
        if points > best[0]:
            best = (points, url, label)

    if best[0] <= 0:
        return org, None, None, "no support link found"
    return org, best[1], preferred_label(org.get("model"), best[2]), f"score {best[0]}"


def main():
    slugs = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = "--force" in sys.argv

    with connect() as conn, conn.cursor() as cur:
        query = "SELECT id, slug, url, model, support_url, support_source FROM orgs"
        params = []
        if slugs:
            query += " WHERE slug = ANY(%s)"
            params.append(slugs)
        cur.execute(query + " ORDER BY slug", params)
        cols = ("id", "slug", "url", "model", "support_url", "support_source")
        orgs = [dict(zip(cols, r)) for r in cur.fetchall()]

    # A hand-set link in orgs.yaml is authoritative; don't re-crawl over it.
    todo = [o for o in orgs if force or not (o["support_url"] and o["support_source"] == "yaml")]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(discover, todo))

    found = 0
    with connect() as conn, conn.cursor() as cur:
        # Mark yaml-provided links so later runs leave them alone.
        cur.execute(
            "UPDATE orgs SET support_source = 'yaml' "
            "WHERE support_url IS NOT NULL AND support_source IS NULL"
        )
        for org, url, label, detail in results:
            if url:
                cur.execute(
                    "UPDATE orgs SET support_url = %s, support_label = %s, "
                    "support_source = 'discovered', support_checked_at = %s WHERE id = %s",
                    (url, label, datetime.now(timezone.utc), org["id"]),
                )
                found += 1
            else:
                cur.execute(
                    "UPDATE orgs SET support_checked_at = %s WHERE id = %s",
                    (datetime.now(timezone.utc), org["id"]),
                )
            log_fetch(cur, org["slug"], "support", url or org["url"], bool(url), detail)
            print(f"  {org['slug']}: {label or '—'} {url or detail}")

        cur.execute("SELECT count(*) FROM orgs WHERE support_url IS NOT NULL")
        have = cur.fetchone()[0]
    print(f"support: {found} discovered this run; {have} orgs now have a support link")


if __name__ == "__main__":
    main()
