"""Shaping someone else's prose to fit a card.

Headlines and summaries arrive as the newsroom wrote them: em dashes with no
spaces, a byline that says "By" twice, a summary that is three paragraphs or
one word. Nothing here changes what was written — it trims, clips at a
sentence, and counts things in words rather than digits.
"""

import re


def excerpt_paragraphs(text, max_paras=3, max_chars=1100):
    if not text:
        return [], False
    paras = [p for p in text.split("\n\n") if p.strip()]
    out, used = [], 0
    for p in paras[:max_paras]:
        if used + len(p) > max_chars and out:
            break
        out.append(p if used + len(p) <= max_chars else p[: max_chars - used].rsplit(" ", 1)[0] + " […]")
        used += len(p)
    return out, len(out) < len(paras)


_EM_SPACES = re.compile(r"\s*\u2014\s*")


def tighten(text):
    """Close the gaps around em dashes.

    ' \u2014 ' gives the browser two break opportunities and a wide gap that
    reads as a hole in a headline. Closed up, the dash stays with the words
    on either side of it.
    """
    return _EM_SPACES.sub("\u2014", text or "")


_LEADING_BY = re.compile(r"^\s*by[:\s]\s*", re.IGNORECASE)


def credit(author):
    """The names in a byline, without the word the byline already supplies.

    Plenty of feeds put the whole credit line in the author field — "By Bob
    Berwyn", "By Howard Herman, The Berkshire Eagle" — and the byline adds its
    own "By", which reads as "By By Bob Berwyn". Strip a leading one. The
    separator the pattern requires after it keeps "Byron" whole, and a credit
    that is nothing but the word itself is left alone rather than emptied.
    """
    return _LEADING_BY.sub("", author, count=1) or author


_PARA_BREAK = re.compile(r"\n\s*\n")


# Candidate sentence ends. A Latin period is only a candidate \u2014 sentence_ends()
# still has to rule out the abbreviations. The CJK stops carry no such
# ambiguity and are not written with a space after them, so they end a sentence
# on their own \u2014 the Chinese-language outlets publish under the same mastheads
# as the English.
_SENTENCE_END = re.compile(
    r"[.!?][\"'\u201d\u2019)\]]*(?=\s|$)"
    r"|[\u3002\uff01\uff1f][\"'\u201d\u2019)\]\uff09]*"
)


# The word a period is attached to, with any interior periods, so "U.S." and
# "a.m." arrive whole rather than as a bare trailing letter.
_DOTTED_WORD = re.compile(r"([A-Za-z][A-Za-z.]*)\.$")


# What a local paper abbreviates constantly. A period after one of these is
# inside a sentence, not at the end of one.
_ABBREVIATIONS = frozenset(
    """
    mr mrs ms mx dr prof rev fr sr jr st sen rep gov pres amb atty
    sgt lt capt col gen maj cpl det ofc adm hon supt
    ave blvd rd ln ct mt ft apt ste dept univ inst
    inc corp co ltd llc plc assn bros
    jan feb mar apr jun jul aug sept sep oct nov dec
    mon tue tues wed thu thurs fri sat sun
    no nos vs etc al approx est fig vol ed pp cf
    """.split()
)


def _ends_sentence(para, i):
    """Whether the period at para[i] is the end of a sentence.

    Three things say it is not: a known abbreviation ("St.", "Gov."), a
    dotted initialism or a lone initial ("U.S.", "a.m.", "J."), and a
    following word that is lower-case, since an English sentence does not
    start that way.
    """
    word = _DOTTED_WORD.search(para[:i + 1])
    if word:
        token = word.group(1)
        if "." in token or len(token) == 1 or token.lower() in _ABBREVIATIONS:
            return False
    return not para[i + 1:].lstrip()[:1].islower()


def sentence_ends(para):
    """Offsets just past each sentence break in a paragraph."""
    return [
        m.end() for m in _SENTENCE_END.finditer(para)
        if para[m.start()] != "." or _ends_sentence(para, m.start())
    ]


SUMMARY_BUDGET = 400


# How far a summary may run past the budget to finish the sentence it is in.
# Overshooting by a line reads better than handing the reader half a sentence.
SUMMARY_GRACE = 120


def clip_summary(text, budget=SUMMARY_BUDGET):
    """The first paragraph of a source summary, held to a budget and cut only
    at the end of a sentence.

    Enough for a reader to judge the story; never enough that they need not
    click through for the rest.
    """
    if not text:
        return ""
    para = _PARA_BREAK.split(text.strip(), maxsplit=1)[0].strip()
    if len(para) <= budget:
        return para
    ends = sentence_ends(para)
    fits = [e for e in ends if e <= budget]
    if fits:
        return para[:fits[-1]].rstrip()
    # Nothing ends inside the budget, so spend the grace to close the first
    # sentence rather than break it.
    if ends and ends[0] <= budget + SUMMARY_GRACE:
        return para[:ends[0]].rstrip()
    # A paragraph that runs on with no sentence break anywhere near the
    # budget: cut at the last complete word and say so, rather than let the
    # cut land mid-token and unmarked.
    cut = para.rfind(" ", 0, budget)
    return (para[:cut].rstrip() + "\u2026") if cut > 0 else para


def about_opening(text, max_chars=420):
    """One paragraph of an About page, for somewhere that has room for one.

    The first block is often the page's own heading, a tagline, or a stray
    line of CMS furniture — a third of the catalog's About texts open that
    way. Where a whole About page can carry that and recover in the next
    paragraph, a single-paragraph quote cannot, so skip to the first
    paragraph long enough to be a description.
    """
    if not usable_about(text):
        return ""
    paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) >= 80]
    excerpt, _ = excerpt_paragraphs("\n\n".join(paras), max_paras=1, max_chars=max_chars)
    return excerpt[0] if excerpt else ""


def and_list(items):
    """A, B and C — the way a sentence names a handful of things."""
    items = list(items)
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def newsroom_phrase(names, cap=4):
    """Who a reporter publishes with, without listing thirty of them."""
    if not names:
        return ""
    if len(names) <= cap:
        return f"Publishes with {and_list(names)}."
    rest = len(names) - cap
    others = "another newsroom" if rest == 1 else f"{rest} other newsrooms"
    return f"Publishes with {', '.join(names[:cap])} and {others}."


def _count(n, noun, plural=None):
    return f"{n} {noun}" if n == 1 else f"{n} {plural or noun + 's'}"


# Plenty of About pages scrape down to a cookie notice or "we have turned
# off comments". Show the quotation only when there is really something there.
_ABOUT_JUNK = re.compile(
    r"turned off comments|comment(ing)? (is|has been) (disabled|closed)|"
    r"cookies?|privacy policy|javascript|subscribe to (our|the) newsletter|"
    r"page not found|404", re.IGNORECASE)


def usable_about(text):
    text = (text or "").strip()
    if len(text) < 240:
        return False
    head = text[:400]
    return not _ABOUT_JUNK.search(head)
