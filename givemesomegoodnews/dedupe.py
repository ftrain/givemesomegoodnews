"""Telling one story from two copies of it.

Syndication and co-publishing mean the same piece arrives from several
newsrooms, sometimes retitled. Headline-token overlap decides what is a
reprint, what is two newsrooms circling one event, and what is neither;
embedding similarity alone cannot, which is what classify_pair explains.
"""

import os
import re


MIN_RELATED_SIM = float(os.environ.get("MIN_RELATED_SIM", "0.30"))


# Above this cosine similarity, or with near-identical headlines, two
# articles are the same story running in multiple outlets (syndication or a
# co-publish), not two newsrooms independently circling one topic.
SAME_STORY_SIM = float(os.environ.get("SAME_STORY_SIM", "0.80"))


def title_tokens(title):
    from .embedder import _STOPWORDS, _WORD_RE

    # Normalize typographic apostrophes and strip possessives, so one
    # outlet's "West's" matches another's "West’s".
    norm = title.lower().replace("’", "'").replace("‘", "'")
    words = (re.sub(r"'s$", "", w).replace("'", "") for w in _WORD_RE.findall(norm))
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def classify_pair(sim, title_a, title_b):
    """'same' = one story in two outlets; 'kindred' = distinct stories that
    rhyme; None = too weak to show. Embedding similarity alone can't split
    reprints from echoes (a retitled reprint scores ~0.89 but co-published
    copies with differently-truncated summaries score ~0.6, while
    independent coverage of one event scores ~0.35), so headlines carry
    half the decision."""
    a, b = title_tokens(title_a), title_tokens(title_b)
    union = a | b
    jac = len(a & b) / len(union) if union else 0.0
    if sim >= SAME_STORY_SIM:
        return "same"
    if jac >= 0.75 and min(len(a), len(b)) >= 4:
        return "same"
    if sim >= 0.50 and jac >= 0.40:
        return "same"
    if sim >= MIN_RELATED_SIM and (len(a & b) >= 1 or sim >= 0.45):
        # The shared-headline-token guard keeps out spurious hashing
        # collisions between short or unrelated titles.
        return "kindred"
    return None


def collapse_duplicates(articles):
    """Fold reprints of one story into a single feed entry.

    Syndication and co-publishing mean the same headline arrives from
    several newsrooms; showing it four times makes the feed look broken.
    The first copy (newest, since the list is already ordered) is kept and
    the rest are listed under it as "Also in". Headline-token overlap
    decides — the same measure classify_pair() uses for reprints.
    """
    kept = []
    for a in articles:
        tokens = title_tokens(a["title"])
        match = None
        if len(tokens) >= 4:
            for candidate in kept:
                other = candidate["_tokens"]
                union = tokens | other
                if not union or len(other) < 4:
                    continue
                if len(tokens & other) / len(union) >= 0.75:
                    match = candidate
                    break
        if match:
            match["_also"].append(a)
        else:
            entry = dict(a)
            entry["_tokens"] = tokens
            entry["_also"] = []
            kept.append(entry)
    return kept
