"""One sentence per newsroom, taken from how it describes itself.

The catalog quotes each About page at length, but the feed needs a single
line. Rather than paraphrase — the whole point is that these newsrooms are
described in their own words — this picks the one sentence from their About
page that does the most work, and quotes that.

No language model. A definitional sentence has a recognisable shape: it
names the outlet, says "is a", and uses the vocabulary of the trade
(nonprofit, newsroom, covering, independent). Boilerplate has an equally
recognisable shape — donation asks, newsletter signups, privacy notices —
and is scored down hard.

Run `python3 -m givemesomegoodnews.taglines` to recompute for every org.
"""

import re
import sys

from .db import connect

# Abbreviations that must not end a sentence.
_ABBREV = r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bSt)(?<!\bInc)(?<!\bCo)(?<!\bU\.S)(?<!\bD\.C)"
_SPLIT = re.compile(_ABBREV + r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")

DEFINITIONAL = re.compile(r"\b(is|are|was|remains)\s+(a|an|the)\b", re.IGNORECASE)
FIRST_PERSON = re.compile(r"\bwe\s+(are|cover|report|publish|believe|serve)\b", re.IGNORECASE)

# Vocabulary of the thing itself.
GOOD_TERMS = [
    (r"\bnon-?profit\b", 3), (r"\bnewsroom\b", 3), (r"\bjournalism\b", 2),
    (r"\bnews (organization|outlet|site|organisation)\b", 3),
    (r"\bindependent\b", 2), (r"\breader-(supported|funded)\b", 2),
    (r"\bworker-owned\b", 2), (r"\bcooperative\b", 2), (r"\bnonpartisan\b", 2),
    (r"\bcover(s|ing|age)?\b", 2), (r"\breport(s|ing)\b", 2),
    (r"\bdedicated to\b", 2), (r"\bmission\b", 1), (r"\bpublishe(s|d|r)\b", 1),
    (r"\b(local|community|statewide|neighborhood)\b", 1),
    (r"\baccountability\b", 1), (r"\binvestigat(e|ive|ions?)\b", 1),
]

# Boilerplate: the sentence is about the website, not the newsroom.
BAD_TERMS = [
    (r"\bdonat(e|ion|ions)\b", 8), (r"\bsubscrib(e|ing|ers?)\b", 6),
    (r"\bnewsletter\b", 8), (r"\bsign up\b", 8), (r"\bclick\b", 8),
    (r"\bcookies?\b", 10), (r"\bprivacy\b", 10), (r"\ball rights reserved\b", 10),
    (r"\badvertis(e|ing)\b", 6), (r"\b(jobs|careers|internships?)\b", 6),
    (r"\bfollow us\b", 8), (r"\bcontact us\b", 8), (r"\bemail\b", 5),
    (r"\btax[- ]deductible\b", 8), (r"\b501\(c\)\b", 4), (r"\bEIN\b", 8),
    (r"https?://", 6), (r"\S+@\S+\.\w+", 6), (r"©", 10),
    (r"^\s*(if you|please|want to|help us|support us|join us)\b", 8),
]

MIN_LEN, MAX_LEN, IDEAL_LO, IDEAL_HI = 40, 300, 70, 220


def sentences(text):
    text = " ".join((text or "").split())
    return [s.strip() for s in _SPLIT.split(text) if s.strip()]


def score(sentence, org_name="", position=0):
    s = sentence
    total = 0.0
    for pattern, weight in GOOD_TERMS:
        if re.search(pattern, s, re.IGNORECASE):
            total += weight
    for pattern, weight in BAD_TERMS:
        if re.search(pattern, s, re.IGNORECASE):
            total -= weight
    if DEFINITIONAL.search(s):
        total += 3
    if FIRST_PERSON.search(s):
        total += 2
    # Naming itself is the strongest signal that this is the definition.
    first_word = (org_name or "").split()
    if org_name and org_name.lower() in s.lower():
        total += 4
    elif first_word and len(first_word[0]) > 3 and first_word[0].lower() in s.lower():
        total += 2
    if IDEAL_LO <= len(s) <= IDEAL_HI:
        total += 2
    # Leads define; footers disclaim.
    total += max(0, 3 - position)
    return total


def best_sentence(about_text, org_name=""):
    """The one sentence that best describes this newsroom, or None."""
    candidates = [s for s in sentences(about_text) if MIN_LEN <= len(s) <= MAX_LEN]
    if not candidates:
        return None
    ranked = sorted(
        ((score(s, org_name, i), -i, s) for i, s in enumerate(candidates)),
        reverse=True,
    )
    best_score, _, best = ranked[0]
    return best if best_score > 0 else None


def main():
    force = "--force" in sys.argv
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, slug, name, about_text, tagline FROM orgs "
            "WHERE about_text IS NOT NULL ORDER BY slug"
        )
        rows = cur.fetchall()
        written = 0
        for org_id, slug, name, about, existing in rows:
            if existing and not force:
                continue
            line = best_sentence(about, name)
            if line:
                cur.execute("UPDATE orgs SET tagline = %s WHERE id = %s", (line, org_id))
                written += 1
                print(f"  {slug}: {line[:110]}")
        cur.execute("SELECT count(*) FROM orgs WHERE tagline IS NOT NULL")
        print(f"taglines: wrote {written}; {cur.fetchone()[0]} orgs now have one")


if __name__ == "__main__":
    main()
