"""RSS out, plus the small files a site is expected to have.

The feeds are the point of politeness here: everything links to the
newsroom that reported it, every item names them as its source, and every
item carries the link that lets a reader pay them. Nothing in a feed
credits this site for the reporting, because none of it is ours.
"""

from datetime import datetime, timezone
from html import escape as esc
from xml.sax.saxutils import quoteattr

from . import config

RSS_ITEMS = 60
RFC822 = "%a, %d %b %Y %H:%M:%S %z"


def _date(value):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(RFC822)


def _item(article, site_url):
    link = article["url"]
    parts = [
        "<item>",
        f"<title>{esc(article['title'])}</title>",
        f"<link>{esc(link)}</link>",
        f'<guid isPermaLink="true">{esc(link)}</guid>',
        f"<pubDate>{_date(article['published_at'] or article['fetched_at'])}</pubDate>",
    ]
    if article.get("author"):
        parts.append(f"<dc:creator>{esc(article['author'])}</dc:creator>")
    if article.get("subject"):
        parts.append(f"<category>{esc(article['subject'])}</category>")
    # Name the newsroom as the source, pointing at their own feed.
    if article.get("org_feed"):
        parts.append(
            f"<source url={quoteattr(article['org_feed'])}>{esc(article['org_name'])}</source>"
        )
    if article.get("image_file"):
        image = f"{site_url}/img/{article['image_file']}"
        parts.append(f'<media:content url={quoteattr(image)} medium="image" />')

    support = article.get("support_url") or article["org_url"]
    label = article.get("support_label") or "Support"
    body = [f"<p><strong>{esc(article['org_name'])}</strong></p>"]
    if article.get("summary"):
        body.append(f"<p>{esc(article['summary'][:600])}</p>")
    body.append(
        f'<p><a href="{esc(link)}">Read it at {esc(article["org_name"])}</a>'
        f' &middot; <a href="{esc(support)}">{esc(label)}</a></p>'
    )
    parts.append(f"<description>{esc(''.join(body))}</description>")
    parts.append("</item>")
    return "\n".join(parts)


def render_rss(articles, title, description, self_path, site_url):
    """A complete RSS 2.0 document."""
    items = "\n".join(_item(a, site_url) for a in articles[:RSS_ITEMS])
    built = _date(None)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/">
<channel>
<title>{esc(title)}</title>
<link>{esc(site_url)}/</link>
<atom:link href={quoteattr(f"{site_url}/{self_path}")} rel="self" type="application/rss+xml" />
<description>{esc(description)}</description>
<language>en-us</language>
<lastBuildDate>{built}</lastBuildDate>
<ttl>60</ttl>
<generator>givemesomegoodnews</generator>
{items}
</channel>
</rss>
"""


# A dot, in the same red the site uses for links. Scales to any tab.
FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" fill="#fff"/>
<circle cx="16" cy="16" r="9" fill="#c8102e"/>
</svg>
"""

# Readers are welcome; scrapers taking the newsrooms' work are not. The
# stories are not ours to hand over in bulk, so crawling of articles and
# feeds is disallowed and the AI crawlers are refused outright.
ROBOTS = """# The reporting linked from this site belongs to the newsrooms that did it.
# Please read it there. Bulk crawling of the feed, the stories, or the
# generated RSS is not permitted.

User-agent: GPTBot
Disallow: /
User-agent: ClaudeBot
Disallow: /
User-agent: anthropic-ai
Disallow: /
User-agent: CCBot
Disallow: /
User-agent: Google-Extended
Disallow: /
User-agent: PerplexityBot
Disallow: /
User-agent: Bytespider
Disallow: /
User-agent: Amazonbot
Disallow: /
User-agent: meta-externalagent
Disallow: /
User-agent: Applebot-Extended
Disallow: /

User-agent: *
Disallow: /search
Disallow: /feed-
Disallow: /subjects/
Disallow: /img/
Disallow: /text/
Disallow: /*.xml$
Allow: /$
Allow: /catalog.html
Allow: /catalog/
Allow: /map.html
Allow: /resources.html
Allow: /features/
Allow: /orgs/
Crawl-delay: 10

Sitemap: {site_url}/sitemap.xml
"""


def render_sitemap(paths, site_url):
    """Only the catalog pages — the stories belong to their newsrooms."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls = "\n".join(
        f"<url><loc>{esc(site_url)}/{esc(p)}</loc><lastmod>{now}</lastmod></url>"
        for p in paths
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"{urls}\n</urlset>\n")
