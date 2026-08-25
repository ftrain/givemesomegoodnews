"""Extract readable text from HTML using only the standard library.

Builds a small DOM, finds the densest content container (<main>, <article>,
or the element whose descendants hold the most paragraph text), and returns
its block-level text as a list of paragraphs. About pages are simple enough
that this beats pulling in a full readability dependency.
"""

import re
from html import unescape
from html.parser import HTMLParser

SKIP_TAGS = {
    "script", "style", "noscript", "svg", "iframe", "form", "nav", "header",
    "footer", "aside", "button", "select", "option", "template", "video",
    "audio", "canvas", "input", "label", "dialog", "menu", "figure", "figcaption",
}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input", "source", "wbr", "area", "base", "col", "embed", "track", "param"}
BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div",
    "section", "article", "main", "tr", "dt", "dd", "pre", "td", "th", "ul", "ol", "table", "body",
}
_CONTENT_HINT = re.compile(r"about|content|entry|article|post|page-|main|body|mission|story", re.I)


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.parent = parent


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", [], None)
        self.cur = self.root
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if self.skip_depth:
            if tag in SKIP_TAGS or tag not in VOID_TAGS:
                if tag not in VOID_TAGS:
                    self.skip_depth += 1
            return
        if tag in SKIP_TAGS:
            self.skip_depth = 1
            return
        node = _Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID_TAGS:
            self.cur = node

    def handle_endtag(self, tag):
        if self.skip_depth:
            if tag not in VOID_TAGS:
                self.skip_depth -= 1
            return
        # Close up to the nearest matching open tag; tolerate bad nesting.
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        if self.skip_depth or not data.strip():
            return
        self.cur.children.append(data)


def _walk_text(node, out):
    """Append text to out, flushing a paragraph break at block boundaries."""
    for child in node.children:
        if isinstance(child, str):
            out.append(child)
        else:
            if child.tag in BLOCK_TAGS:
                out.append("\n")
            _walk_text(child, out)
            if child.tag in BLOCK_TAGS or child.tag == "br":
                out.append("\n")


def _para_text_len(node):
    total = 0
    for child in node.children:
        if isinstance(child, str):
            if node.tag == "p":
                total += len(child)
        else:
            total += _para_text_len(child)
    return total


def _collect_candidates(node, out):
    for child in node.children:
        if isinstance(child, str):
            continue
        idcls = f"{child.attrs.get('id', '')} {child.attrs.get('class', '')}"
        if child.tag in ("main", "article") or (
            child.tag in ("div", "section") and _CONTENT_HINT.search(idcls)
        ):
            out.append(child)
        _collect_candidates(child, out)


def _strip_nul(text):
    """Postgres text columns cannot hold NUL, and some pages emit them."""
    return text.replace("\x00", "") if text else text


def paragraphs_from_html(html):
    builder = _TreeBuilder()
    try:
        builder.feed(html)
    except Exception:
        pass
    candidates = []
    _collect_candidates(builder.root, candidates)
    best = max(candidates, key=_para_text_len, default=builder.root)
    if _para_text_len(best) < 200:
        best = builder.root

    pieces = []
    _walk_text(best, pieces)
    text = unescape("".join(pieces))
    paras = []
    for raw in text.split("\n"):
        p = re.sub(r"\s+", " ", raw).strip()
        p = _strip_nul(p)
        if len(p) >= 25 and p not in paras:
            paras.append(p)
    return paras


_TAGISH = re.compile(r"<[a-zA-Z/][^>]*>")


def text_from_html_fragment(html):
    """Flatten an HTML fragment (e.g. an RSS summary) to plain text.

    Some publishers double-escape their markup, so the feed carries
    `&lt;p&gt;` where it means a paragraph. One pass turns that back into a
    literal "<p>" sitting in what is supposed to be plain text — which is
    how raw tags ended up in stored summaries. Flatten repeatedly until
    nothing tag-shaped survives.
    """
    text = html or ""
    for _ in range(3):
        builder = _TreeBuilder()
        try:
            builder.feed(text)
        except Exception:
            pass
        pieces = []
        _walk_text(builder.root, pieces)
        flat = _strip_nul(re.sub(r"\s+", " ", unescape("".join(pieces))).strip())
        if not _TAGISH.search(flat):
            return flat
        text = flat
    # Pathological nesting: strip anything tag-shaped outright.
    return re.sub(r"\s+", " ", _TAGISH.sub(" ", text)).strip()


# Feed summaries arrive with the CMS's own furniture attached. WordPress
# appends "The post <headline> appeared first on <outlet>."; several themes
# leave a truncation marker behind. None of it is the story.
_BOILERPLATE = [
    re.compile(r"\s*The post\s+.*?\s+appeared first on\s+[^.]*\.?\s*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\s*Continue reading\s*[\u2192>\u00bb]*\s*$", re.IGNORECASE),
    re.compile(r"\s*(\[\s*(\u2026|\.\.\.)\s*\]|\u2026|\.\.\.)\s*$"),
    re.compile(r"\s*Read more\s*$", re.IGNORECASE),
    # "Read the story on VTDigger here: <headline>", and the syndication
    # footers that read "This article first appeared on ...".
    re.compile(r"(?<=.{40})\s*Read (?:the|this)(?: full)?(?: story| article)?"
               r"(?: on| at| from)\s+\S.*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?<=.{40})\s*This (?:article|story|post)(?: first| originally)?"
               r" appeared\s+\S.*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"(?<=.{40})\s*Originally published\s+\S.*$", re.IGNORECASE | re.DOTALL),
    # Publishers often truncate mid-footer, leaving "[\u2026] The post Akron teens"
    # with no "appeared first on" to anchor on. Drop from "The post" to the
    # end, but only once there is real summary text ahead of it.
    re.compile(r"(?<=.{40})\s*The post\s+\S.*$", re.IGNORECASE | re.DOTALL),
]


def clean_summary(text):
    """Strip CMS furniture from a feed summary. Returns '' if nothing is left."""
    out = (text or "").strip()
    for _ in range(3):  # boilerplate often stacks: "... [\u2026] The post ..."
        before = out
        for pattern in _BOILERPLATE:
            out = pattern.sub("", out).strip()
        if out == before:
            break
    return out
