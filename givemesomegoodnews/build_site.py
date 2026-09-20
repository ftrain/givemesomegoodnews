"""Generate the static site from the database: plain HTML, no CSS, no JS.

Pages:
    site/index.html         the feed itself, newest first (page 1)
    site/catalog.html       every org, in their own words, grouped by state
    site/map.html           inline-SVG coverage map (Albers projection)
    site/feed-2.html ...     the rest of the feed
    site/connections.html   strongest story pairs across regions (pgvector)
    site/trending.html      the newest hourly trending snapshot (see trending.py)
    site/topics/<slug>.html one tag page per trending topic, current and recent
    site/orgs/<slug>.html   one page per org
    site/onepage.html       everything on one self-contained page
    data/catalog.json       machine-readable catalog export
"""

import collections
import json
import re
import shutil
from html import escape as esc

from . import config, filters, syndicate
from .cards import (CADENCE, HOUSE_IMAGE_USES, REPORTERS, cadence_by_org,
                    load_reporter_panels, ownership_tags, render_feed)
from .db import connect
from .dedupe import collapse_duplicates
from .links import feed_page_name
from .pages import (ONEPAGE_ARTICLES, group_orgs_by_state, render_about,
                    render_big_stories, render_catalog_index, render_feeds_page,
                    render_institutions, render_map, render_org_list,
                    render_org_page, render_story_links, write_text_edition)
from .shell import FEED_SCRIPT, MENU_FEEDS, MENU_SUBJECTS, page, search_form
from .tags import TAG_GROUPS, tag_slug
from .topics import (load_topics, render_home_topics, render_trending, set_topics,
                     topic_intro)

FEED_PAGE_ARTICLES = 600
# Items per feed page — the rest arrive as you scroll.
FEED_PAGE_SIZE = 30



















































def write_feed_pages(site, cur, articles, stem, title, heading, prefix="",
                     skip_images=(), subdir=None,
                     first_name=None, intro="", with_related=True,
                     feed_href="feed.xml", feed_title=None, show_heading=True,
                     current_topic=None):
    """Split a feed into pages so no single page carries the whole crawl."""
    target = (site / subdir) if subdir else site
    target.mkdir(parents=True, exist_ok=True)
    chunks = [articles[i:i + FEED_PAGE_SIZE] for i in range(0, len(articles), FEED_PAGE_SIZE)] or [[]]
    for index, chunk in enumerate(chunks):
        body = render_feed(cur, chunk, prefix=prefix, heading=heading,
                           skip_images=skip_images,
                           page_index=index, page_count=len(chunks), stem=stem,
                           intro=intro, with_related=with_related,
                           show_heading=show_heading)
        head = title if index == 0 else f"{title} — page {index + 1}"
        name = first_name if (index == 0 and first_name) else feed_page_name(stem, index)
        target.joinpath(name).write_text(
            page(head, body, prefix=prefix, scripts=FEED_SCRIPT,
                 feed_href=feed_href, feed_title=feed_title, current_topic=current_topic)
        )
    return len(chunks)
















































































# --- trending topics ---------------------------------------------------------
# A topic is a tag like any other: a lozenge on the cards it covers, a page of
# full cards at topics/<slug>.html. What makes it different is that it comes
# and goes by the hour, from the snapshot givemesomegoodnews.trending writes.































def write_topic_pages(site, cur, snapshot, current, former, skip_images=()):
    """One tag page of full cards per topic, current and recently former;
    pages for topics older than that are removed. Returns each topic's
    stories, folded, keyed by slug."""
    target = site / "topics"
    target.mkdir(parents=True, exist_ok=True)
    written, stories = set(), {}
    for topic, is_current in [(t, True) for t in current] + [(t, False) for t in former]:
        articles = collapse_duplicates(load_articles(
            cur, len(topic["article_ids"]), ids=topic["article_ids"],
            apply_filters=False, language=None))
        stories[topic["slug"]] = articles
        n_pages = write_feed_pages(
            site, cur, articles, topic["slug"],
            f"{config.SITE_NAME} — {topic['label']}", topic["label"], prefix="../",
            skip_images=skip_images, subdir="topics", with_related=False,
            intro=topic_intro(topic, snapshot, current=is_current),
            current_topic=topic["slug"],
        )
        written.update(feed_page_name(topic["slug"], i) for i in range(n_pages))
    for stale in target.glob("*.html"):
        if stale.name not in written:
            stale.unlink()
    return stories












































ORG_COLUMNS = (
    "id", "slug", "name", "url", "about_url", "feed_url", "city", "state", "lat", "lon",
    "geo_precision", "coverage", "coverage_type", "model", "affiliations", "founded",
    "support_url", "support_label", "features", "source", "tagline", "beat",
    "about_text", "about_source_url", "about_fetched_at",
)


def load_orgs(cur):
    cur.execute(f"SELECT {', '.join(ORG_COLUMNS)} FROM orgs ORDER BY name")
    return [dict(zip(ORG_COLUMNS, row)) for row in cur.fetchall()]


def load_articles(cur, limit, subject=None, feature=None, default_only=False,
                  apply_filters=True, language="English", ids=None):
    filter_sql, filter_params = filters.where_clause(cur) if apply_filters else ("", [])
    cur.execute(
        """
        SELECT a.id, a.url, a.title, a.summary, a.author, a.published_at, a.fetched_at,
               a.image_file, a.image_w, a.image_h, a.image_alt, a.subject,
               o.id AS org_id, o.name AS org_name, o.slug, o.url AS org_url,
               o.support_url, o.support_label,
               o.state, o.city, o.beat, o.coverage, o.coverage_type,
               o.lat, o.lon, o.geo_precision,
               o.timezone, o.model, o.features, o.feed_url, o.in_default, o.language,
               o.about_text
        FROM articles a JOIN orgs o ON o.id = a.org_id
        WHERE (%s::text IS NULL OR a.subject = %s)
          AND (%s::text IS NULL OR %s = ANY(o.features))
          AND (NOT %s OR o.in_default)
          AND (%s::text IS NULL OR coalesce(a.language, 'English') = %s)
          AND (%s::int[] IS NULL OR a.id = ANY(%s))
          {extra}
        ORDER BY coalesce(a.published_at, a.fetched_at) DESC, a.id DESC
        LIMIT %s
        """.format(extra=("AND " + filter_sql) if filter_sql else ""),
        [subject, subject, feature, feature, default_only, language, language,
         ids, ids, *filter_params, limit],
    )
    cols = ("id", "url", "title", "summary", "author", "published_at", "fetched_at",
            "image_file", "image_w", "image_h", "image_alt", "subject",
            "org_id", "org_name", "slug", "org_url",
            "support_url", "support_label", "state", "city", "beat", "coverage",
            "coverage_type", "lat", "lon", "geo_precision",
            "timezone", "model", "features", "org_feed",
            "in_default", "language", "about_text")
    return [dict(zip(cols, row)) for row in cur.fetchall()]




def export_catalog_json(orgs):
    out = []
    for o in orgs:
        rec = {k: o[k] for k in ORG_COLUMNS if k not in ("id",)}
        if rec["about_fetched_at"]:
            rec["about_fetched_at"] = rec["about_fetched_at"].isoformat()
        out.append(rec)
    path = config.DATA_DIR / "catalog.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return path


def main():
    site = config.SITE_DIR
    (site / "orgs").mkdir(parents=True, exist_ok=True)

    fonts_src = config.ASSETS_DIR / "fonts"
    if fonts_src.is_dir():
        fonts_dst = site / "fonts"
        fonts_dst.mkdir(parents=True, exist_ok=True)
        wanted = {f.name for f in fonts_src.glob("*.woff2")}
        for font in fonts_src.glob("*.woff2"):
            shutil.copyfile(font, fonts_dst / font.name)
        # Drop faces that are no longer part of the design, so a typeface
        # change does not leave the old files being served forever.
        for stale in fonts_dst.glob("*.woff2"):
            if stale.name not in wanted:
                stale.unlink()
    flags_src = config.ASSETS_DIR / "flags"
    if flags_src.is_dir():
        flags_dst = site / "flags"
        flags_dst.mkdir(parents=True, exist_ok=True)
        wanted = {f.name for f in flags_src.glob("*.webp")}
        for flag in flags_src.glob("*.webp"):
            shutil.copyfile(flag, flags_dst / flag.name)
        # A state that redraws its flag leaves the old one behind otherwise,
        # and so does the earlier run that wrote these out as SVG.
        for stale in flags_dst.glob("*"):
            if stale.is_file() and stale.name not in wanted:
                stale.unlink()
    masthead = config.ASSETS_DIR / "masthead.svg"
    if masthead.is_file():
        shutil.copyfile(masthead, site / "masthead.svg")
    # The share card is drawn by givemesomegoodnews.share_card and lives in
    # the repository; the build only carries it across, the way it carries
    # the flags. Every page points at it, so it has to be here before any
    # of them are written.
    share = config.ASSETS_DIR / config.SHARE_IMAGE
    if share.is_file():
        shutil.copyfile(share, site / config.SHARE_IMAGE)

    with connect() as conn, conn.cursor() as cur:
        orgs = load_orgs(cur)
        # One query for every byline on the site, before a single card is
        # rendered. Feed pages, tag pages, subject pages and the plain-text
        # edition all read this dictionary; none of them asks again.
        REPORTERS.clear()
        REPORTERS.update(load_reporter_panels(cur))
        # How often each newsroom publishes: one grouped query for the whole
        # build, read from the mapping as each card renders.
        CADENCE.clear()
        CADENCE.update(cadence_by_org(cur))
        # The trending topics, likewise: the bar on every page and the tag on
        # every card read them, so they are set before the first page.
        snapshot, current_topics, former_topics = load_topics(cur)
        set_topics(current_topics)
        articles = collapse_duplicates(
            load_articles(cur, FEED_PAGE_ARTICLES, default_only=True))
        all_articles = collapse_duplicates(
            load_articles(cur, FEED_PAGE_ARTICLES, apply_filters=False))

        # An image reused across many stories is the newsroom's house art or
        # a category placeholder, not this story's picture. Don't repeat it.
        cur.execute(
            "SELECT image_file FROM articles WHERE image_file IS NOT NULL "
            "GROUP BY image_file HAVING count(*) >= %s",
            (HOUSE_IMAGE_USES,),
        )
        house_images = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT count(*) FROM articles")
        n_articles = cur.fetchone()[0]
        n_states = len({o["state"] for o in orgs if o["state"]})
        intro = (f"<p><small>{len(orgs)} newsrooms · {n_states} states and D.C. · "
                 f"{n_articles} stories</small></p>")

        # Which newsrooms published in the last day — the map lights those red.
        cur.execute(
            "SELECT o.slug FROM articles a JOIN orgs o ON o.id = a.org_id "
            "WHERE coalesce(a.published_at, a.fetched_at) > now() - interval '24 hours' "
            "GROUP BY o.slug"
        )
        recent = {r[0] for r in cur.fetchall()}

        (site / "catalog.html").write_text(
            page(f"{config.SITE_NAME} — Catalog", render_catalog_index(orgs))
        )
        # One page per state, so the catalog stays small however big it gets.
        (site / "catalog").mkdir(parents=True, exist_ok=True)
        state_groups, _national = group_orgs_by_state(orgs)
        for state_name, group in state_groups.items():
            body = (f"<h1>{esc(state_name)}</h1>"
                    f"<p>{len(group)} newsrooms. "
                    f'<a href="../catalog.html">All states</a></p>'
                    + render_org_list(group, prefix="../"))
            slug = re.sub(r"[^a-z0-9]+", "-", state_name.lower()).strip("-")
            (site / "catalog" / f"{slug}.html").write_text(
                page(f"{config.SITE_NAME} — {state_name}", body, prefix="../")
            )
        # And one page per feature: Black-owned, Spanish, INN member...
        (site / "features").mkdir(parents=True, exist_ok=True)
        by_feature = collections.defaultdict(list)
        for org in orgs:
            for feature in ownership_tags(org):
                by_feature[feature].append(org)
        for feature, group in by_feature.items():
            body = (f"<h1>{esc(feature)}</h1>"
                    f"<p>{len(group)} newsrooms. "
                    f'<a href="../catalog.html">Whole catalog</a></p>'
                    + render_org_list(sorted(group, key=lambda o: o["name"]), prefix="../"))
            slug = re.sub(r"[^a-z0-9]+", "-", feature.lower()).strip("-")
            (site / "features" / f"{slug}.html").write_text(
                page(f"{config.SITE_NAME} — {feature}", body, prefix="../")
            )
        (site / "resources.html").write_text(
            page(f"{config.SITE_NAME} — Resources", render_institutions(cur, orgs),
                 description="The funders, networks, associations and directories behind "
                             "the newsrooms in this catalog.")
        )
        (site / "map.html").write_text(
            page(f"{config.SITE_NAME} — Coverage map", render_map(orgs, recent=recent))
        )
        cur.execute(
            "SELECT subject, count(*) FROM articles WHERE subject IS NOT NULL "
            "GROUP BY subject ORDER BY subject"
        )
        subject_counts = cur.fetchall()

        MENU_SUBJECTS[:] = [
            (name, re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
            for name, _n in subject_counts
        ]
        MENU_FEEDS[:] = [("feed.xml", "Everything")] + [
            (f"subjects/{slug}.xml", name) for name, slug in MENU_SUBJECTS
        ]

        # No headline, no counts, no subject bar: the front page opens on
        # the first story and everything else lives behind the menu.
        n_feed_pages = write_feed_pages(
            site, cur, articles, "feed", config.SITE_NAME, "Feed",
            skip_images=house_images, first_name="index.html",
            show_heading=False, intro=search_form() + render_home_topics(),
        )

        # Everything, including the ordinary commercial weeklies the default
        # view leaves out.
        write_feed_pages(
            site, cur, all_articles, "everything",
            f"{config.SITE_NAME} — Everything", "Every newsroom",
            skip_images=house_images, with_related=False,
            intro='<p class="meta">Every newsroom in the catalog, including the '
                  'commercial weeklies the front page leaves out. '
                  '<a href="/">Back to the default feed</a>.</p>',
        )

        # One feed per tag, so a lozenge is a real destination.
        (site / "tags").mkdir(parents=True, exist_ok=True)
        cur.execute(
            "SELECT unnest(features) AS f, count(*) FROM orgs GROUP BY 1 ORDER BY 2 DESC"
        )
        tag_counts = dict(cur.fetchall())
        for _group, group_tags in TAG_GROUPS:
            for tag in group_tags:
                tag_articles = collapse_duplicates(
                    load_articles(cur, FEED_PAGE_ARTICLES, feature=tag)
                )
                if not tag_articles:
                    continue
                n_rooms = tag_counts.get(tag, 0)
                write_feed_pages(
                    site, cur, tag_articles, tag_slug(tag),
                    f"{config.SITE_NAME} — {tag}", tag, prefix="../",
                    skip_images=house_images, subdir="tags", with_related=False,
                    feed_href=f"tags/{tag_slug(tag)}.xml",
                    feed_title=f"{config.SITE_NAME} — {tag}",
                    intro=f'<p class="meta">{n_rooms} newsrooms tagged {esc(tag)}. '
                          f'<a href="../catalog.html">All tags</a></p>',
                )

        for name, _n in subject_counts:
            subject_articles = collapse_duplicates(
                load_articles(cur, FEED_PAGE_ARTICLES, subject=name)
            )
            write_feed_pages(
                site, cur, subject_articles, name.lower().replace(" ", "-"),
                f"{config.SITE_NAME} — {name}", name, prefix="../",
                skip_images=house_images, subdir="subjects",
                feed_href=f"subjects/{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.xml",
                feed_title=f"{config.SITE_NAME} — {name}",
                # One pgvector lookup per item is affordable on the main feed;
                # repeating it for every subject page is what made the build
                # take a quarter of an hour.
                with_related=False,
            )
        (site / "big-stories.html").write_text(page(
            f"{config.SITE_NAME} — Big stories", render_big_stories(cur),
            description="Stories running in several newsrooms at once."))
        topic_stories = write_topic_pages(site, cur, snapshot, current_topics, former_topics,
                                          skip_images=house_images)
        (site / "trending.html").write_text(page(
            f"{config.SITE_NAME} — Trending",
            render_trending(snapshot, current_topics, topic_stories),
            description="What local newsrooms are covering more than usual today."))
        (site / "story-links.html").write_text(page(
            f"{config.SITE_NAME} — Story links", render_story_links(cur),
            description="Separate newsrooms reporting the same pressure."))
        (site / "about.html").write_text(page(
            f"{config.SITE_NAME} — About", render_about(cur, orgs),
            description="What this is, where the data comes from, and who to credit."))

        for org in orgs:
            body = render_org_page(cur, org)
            (site / "orgs" / f"{org['slug']}.html").write_text(
                page(f"{config.SITE_NAME} — {org['name']}", body, prefix="../")
            )

        onepage_articles = articles[:ONEPAGE_ARTICLES]
        onepage = "\n<hr>\n".join(
            [
                f"<h1>{esc(config.SITE_NAME)}</h1>\n{intro}",
                '<div id="catalog">' + render_catalog_index(orgs) + "</div>",
                '<div id="map">' + render_map(orgs, mode="onepage", recent=recent) + "</div>",
                '<div id="feed">' + render_feed(cur, onepage_articles, mode="onepage",
                                                 skip_images=house_images,
                                                 with_related=False) + "</div>",
                '<div id="connections">' + render_big_stories(cur, mode="onepage") + "</div>",
            ]
        )
        onepage_nav = (
            f'<a href="#feed">Feed</a>\n<a href="#catalog">Catalog</a>\n'
            f'<a href="#map">Map</a>\n<a href="#connections">Big stories</a>'
        )
        (site / "onepage.html").write_text(page(config.SITE_NAME, onepage, nav_html=onepage_nav))

        # --- RSS, one per subject plus the whole feed --------------------
        site_url = config.SITE_URL.rstrip("/")
        (site / "feed.xml").write_text(syndicate.render_rss(
            articles, config.SITE_NAME,
            "Local newsrooms built to last, newest first. Every story links back "
            "to the newsroom that reported it.",
            "feed.xml", site_url))
        for name, slug in MENU_SUBJECTS:
            subject_articles = load_articles(cur, syndicate.RSS_ITEMS, subject=name)
            (site / "subjects" / f"{slug}.xml").write_text(syndicate.render_rss(
                subject_articles, f"{config.SITE_NAME} — {name}",
                f"{name} reporting from local newsrooms across the United States.",
                f"subjects/{slug}.xml", site_url))

        # One RSS per tag as well as per subject.
        emitted_tags = []
        for _group, group_tags in TAG_GROUPS:
            for tag in group_tags:
                tag_articles = load_articles(cur, syndicate.RSS_ITEMS, feature=tag)
                if not tag_articles:
                    continue
                emitted_tags.append(tag)
                (site / "tags" / f"{tag_slug(tag)}.xml").write_text(syndicate.render_rss(
                    tag_articles, f"{config.SITE_NAME} — {tag}",
                    f"Stories from newsrooms tagged {tag}.",
                    f"tags/{tag_slug(tag)}.xml", site_url))

        (site / "feeds.html").write_text(page(
            f"{config.SITE_NAME} — RSS feeds",
            render_feeds_page(subject_counts, emitted_tags),
            description="Every RSS feed this site publishes, by subject and by tag."))

        # --- the small files a site is expected to have ------------------
        drawn_icon = config.ASSETS_DIR / "favicon.svg"
        (site / "favicon.svg").write_text(
            drawn_icon.read_text() if drawn_icon.is_file() else syndicate.FAVICON)
        (site / "404.html").write_text(page(
            f"{config.SITE_NAME} — not found",
            "<h1>Not here</h1>"
            "<p>That page has moved or never existed. The menu above has "
            'everything, or start from <a href="/">the feed</a>.</p>'))
        (site / "robots.txt").write_text(syndicate.ROBOTS.format(site_url=site_url))
        sitemap_paths = ["", "catalog.html", "map.html", "resources.html",
                 "big-stories.html", "story-links.html", "about.html", "feeds.html"]
        sitemap_paths += [f"catalog/{re.sub(r'[^a-z0-9]+', '-', n.lower()).strip('-')}.html"
                          for n in state_groups]
        sitemap_paths += [f"features/{re.sub(r'[^a-z0-9]+', '-', f.lower()).strip('-')}.html"
                          for f in by_feature]
        sitemap_paths += [f"orgs/{o['slug']}.html" for o in orgs]
        (site / "sitemap.xml").write_text(syndicate.render_sitemap(sitemap_paths, site_url))

        # --- the plain-text edition --------------------------------------
        write_text_edition(site, cur, articles, orgs)

        path = export_catalog_json(orgs)
        n_img = sum(1 for a in articles if a.get("image_file"))
        n_folded = sum(len(a.get("_also", [])) for a in articles)
        print(f"built site/ ({len(orgs)} orgs, {len(articles)} feed items over {n_feed_pages} pages, "
              f"{n_img} with images, {n_folded} reprints folded in, "
              f"{len(subject_counts)} subjects) and {path.relative_to(config.ROOT)}")


if __name__ == "__main__":
    main()
