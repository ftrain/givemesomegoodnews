"""Trending topics, as tags.

A topic is a tag like Nonprofit or Vermont, with one difference: it comes and
goes by the hour, from the snapshot givemesomegoodnews.trending writes. This
holds the current set — the row under the masthead, the tag in a card's rail,
the section on the front page, the index — and the reading of the snapshot
they all come from. Finding the topics is trending.py's work, not this.
"""

import collections
import re
from datetime import datetime, timedelta, timezone
from html import escape as esc

from .links import _org_line
from .prose import _count, tighten
from .tags import tag_slug
from .timezones import local_time, zone_for


# The current snapshot's topics, ranked, and the topics each article is in.
# Filled in by set_topics() before anything renders — by main() for the
# build, by searchd for its pages — like MENU_SUBJECTS.
TOPICS = []


TOPICS_OF = {}


# Headlines under each topic on trending.html; the topic page has them all.
TRENDING_STORIES = 3


# Topics on the home page's section.
HOME_TOPICS = 6


# The job runs hourly; past this trending.html says its list is late.
TRENDING_STALE_HOURS = 3


# A topic's page outlives its place on the list by this long, so a link
# someone shared this morning still leads somewhere this evening.
TOPIC_PAGES_KEPT_HOURS = 48


def topic_slug(label):
    """A topic's page name. Feed pages continue as <stem>-2.html, <stem>-3.html,
    so a slug may never end in a bare number: "Route 66" would otherwise be
    page 66 of a topic called "Route"."""
    slug = tag_slug(label) or "topic"
    return f"{slug}-topic" if re.search(r"-\d+$", slug) or slug.isdigit() else slug


def topic_href(topic, prefix=""):
    return f"{prefix}topics/{topic['slug']}.html"


def load_topics(cur, kept_hours=TOPIC_PAGES_KEPT_HOURS):
    """The newest snapshot, its topics, and the recent topics no longer in it.

    Returns (snapshot, current, former). A snapshot is None before the first
    run. Every topic carries a unique slug, its counts, its article ids (most
    central first) and its lead story's headline and newsroom; a former topic
    also carries `until`, the last time it was on the list. A label that
    comes back names the same page, so a topic keeps its address across hours.
    """
    cur.execute(
        "SELECT id, generated_at, window_hours, baseline_days, labeler "
        "FROM trending_snapshots ORDER BY generated_at DESC, id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None, [], []
    snapshot = dict(zip(("id", "generated_at", "window_hours", "baseline_days", "labeler"), row))
    cur.execute(
        """
        SELECT t.snapshot_id, s.generated_at, s.labeler, t.label, t.n_stories,
               t.n_newsrooms, t.n_states, t.article_ids
        FROM trending_topics t JOIN trending_snapshots s ON s.id = t.snapshot_id
        WHERE t.snapshot_id = %s OR s.generated_at > %s - make_interval(hours => %s)
        ORDER BY s.generated_at DESC, s.id DESC, t.rank
        """,
        (snapshot["id"], snapshot["generated_at"], kept_hours),
    )
    cols = ("snapshot_id", "until", "labeler", "label", "n_stories", "n_newsrooms",
            "n_states", "article_ids")
    current, former, seen = [], [], set()
    for r in cur.fetchall():
        topic = dict(zip(cols, r))
        slug = topic_slug(topic["label"])
        if topic["snapshot_id"] == snapshot["id"]:
            # Two current topics can reduce to one slug; the second is -b, as
            # a numeric suffix would be taken for the first one's page 2.
            base, n = slug, 1
            while slug in seen:
                slug, n = f"{base}-{chr(ord('a') + n)}", n + 1
            topic["slug"] = slug
            seen.add(slug)
            current.append(topic)
        elif slug not in seen:
            topic["slug"] = slug
            seen.add(slug)
            former.append(topic)

    leads = [t["article_ids"][0] for t in current + former if t["article_ids"]]
    cur.execute(
        "SELECT a.id, a.title, a.url, o.name FROM articles a JOIN orgs o ON o.id = a.org_id "
        "WHERE a.id = ANY(%s)", (leads,))
    by_id = {r[0]: {"title": r[1], "url": r[2], "org_name": r[3]} for r in cur.fetchall()}
    for t in current + former:
        t["lead"] = by_id.get(t["article_ids"][0]) if t["article_ids"] else None
    return snapshot, current, former


def set_topics(topics):
    """Make these the topics every page and card renders from."""
    of = collections.defaultdict(list)
    for topic in topics:
        for article_id in topic["article_ids"]:
            of[article_id].append(topic)
    TOPICS[:] = topics
    TOPICS_OF.clear()
    TOPICS_OF.update(of)


def topic_lozenge(topic, prefix="", current=False):
    here = ' aria-current="page"' if current else ""
    return (f'<a class="lozenge topic" href="{topic_href(topic, prefix)}"{here}>'
            f'{esc(topic["label"])}</a>')


def topic_bar(prefix="", current_topic=None):
    """The current topics as a row of tags under the masthead, on every page;
    on a topic's own page its tag is the one lit."""
    if not TOPICS:
        return ""
    links = "".join(topic_lozenge(t, prefix, t["slug"] == current_topic) for t in TOPICS)
    return (f'<nav class="trendbar" aria-label="Trending topics">'
            f'<a class="trendlabel" href="{prefix}trending.html">Trending</a>{links}</nav>')


def card_topics(a, prefix=""):
    """The current topics a story, or one of the reprints folded into it, is in."""
    ids = [a.get("id")] + [d.get("id") for d in a.get("_also", ())]
    found = []
    for article_id in ids:
        for topic in TOPICS_OF.get(article_id, ()):
            if topic not in found:
                found.append(topic)
    if not found:
        return ""
    return f'<span class="tags">{"".join(topic_lozenge(t, prefix) for t in found)}</span>'


def topic_counts(topic):
    return (f'{_count(topic["n_stories"], "story", "stories")} &middot; '
            f'{_count(topic["n_newsrooms"], "newsroom")} &middot; '
            f'{_count(topic["n_states"], "state")}')


def _stamp(when):
    local = when.astimezone(zone_for(None))
    return (f'<time datetime="{when.isoformat()}" data-pub="{esc(local_time(when))}">'
            f'{local.strftime("%a, %b %-d at %-I:%M %p %Z")}</time>')


def naming_note(labeler):
    if labeler == "terms":
        return "Named by the phrase its headlines share."
    return (f"Named from its headlines by DeepSeek ({esc(labeler)}); which topics rise, "
            f"and every count, are worked out here without it.")


def render_home_topics(prefix=""):
    """The front page's trending section: each topic, its reach, its lead story."""
    if not TOPICS:
        return ""
    items = []
    for topic in TOPICS[:HOME_TOPICS]:
        lead = topic.get("lead")
        lead_html = (f'<span class="lead">{esc(tighten(lead["title"]))} '
                     f'<span class="meta">&mdash; {esc(lead["org_name"])}</span></span>'
                     if lead else "")
        items.append(
            f'<li><a class="name" href="{topic_href(topic, prefix)}">{esc(topic["label"])}</a>'
            f'<span class="meta">{_count(topic["n_newsrooms"], "newsroom")} &middot; '
            f'{_count(topic["n_states"], "state")}</span>{lead_html}</li>'
        )
    more = (f'<a href="{prefix}trending.html">All {len(TOPICS)} trending topics</a>'
            if len(TOPICS) > HOME_TOPICS else
            f'<a href="{prefix}trending.html">How these are found</a>')
    return (f'<section class="trending-now" aria-labelledby="trending-now">'
            f'<h2 id="trending-now">Trending now</h2>'
            f'<ol>{"".join(items)}</ol>'
            f'<p class="meta">What local newsrooms are covering more than usual today. {more}</p>'
            f'</section>')


def topic_intro(topic, snapshot, current=True, prefix="../"):
    """The line above a topic page's cards: what the tag means, and its reach."""
    if current:
        when = (f"trending in the {snapshot['window_hours']} hours to "
                f"{_stamp(snapshot['generated_at'])}")
    else:
        when = f"trending until {_stamp(topic['until'])}, and no longer on the list"
    return (f'<p class="meta"><a class="lozenge topic on" href="{prefix}trending.html">Topic</a> '
            f'{topic_counts(topic)}, {when}. {naming_note(topic["labeler"])} '
            f'<a href="{prefix}trending.html">All trending topics</a></p>')


def render_trending(snapshot, topics, stories_by_topic, prefix="", now=None):
    """The index of topics: each with its reach, first headlines, and page."""
    parts = ["<h1>Trending</h1>"]
    if not snapshot:
        parts.append("<p>What local newsrooms are covering more than usual. The first "
                     "list is made at seven minutes past the hour.</p>")
        return "\n".join(parts)

    when = snapshot["generated_at"]
    now = now or datetime.now(timezone.utc)
    late = (" The hourly update is running late."
            if now - when > timedelta(hours=TRENDING_STALE_HOURS) else "")
    parts.append(
        f"<p>What local newsrooms are covering more than usual: the last "
        f"{snapshot['window_hours']} hours set against the {snapshot['baseline_days']} "
        f"days before, counting newsrooms rather than stories, and a reprint once. "
        f"Made every hour; this list at {_stamp(when)}.{late}</p>"
    )
    if snapshot["labeler"] == "terms":
        parts.append('<p class="meta">Each topic is named by the phrase its headlines share.</p>')
    else:
        parts.append(
            f'<p class="meta">Topic names are written from the headlines by DeepSeek '
            f'({esc(snapshot["labeler"])}). Which topics rise, and every count, are worked '
            f'out here without it.</p>'
        )
    if not topics:
        parts.append("<p>Nothing is running well above its usual level right now.</p>")
        return "\n".join(parts)

    for topic in topics:
        href = topic_href(topic, prefix)
        parts.append(f'<h2><a href="{href}">{esc(topic["label"])}</a></h2>')
        parts.append(f'<p class="meta">{topic_counts(topic)}</p>')
        parts.append("<ul>")
        for story in stories_by_topic.get(topic["slug"], [])[:TRENDING_STORIES]:
            names = list(dict.fromkeys(d["org_name"] for d in story["_also"]
                                       if d["org_name"] != story["org_name"]))
            also_html = ""
            if names:
                more = f" and {len(names) - 3} more" if len(names) > 3 else ""
                also_html = f" &middot; also in {', '.join(esc(n) for n in names[:3])}{more}"
            parts.append(
                f'<li><a href="{esc(story["url"])}">{esc(story["title"])}</a>'
                f'<br><span class="meta">{_org_line(story, "site", prefix)}{also_html}</span></li>'
            )
        parts.append("</ul>")
        parts.append(f'<p class="meta"><a href="{href}">Every story on this topic</a></p>')
    return "\n".join(parts)
