"""Who wrote it — reporter identities resolved from the byline.

`articles.author` is whatever the feed put there, and feeds are inconsistent
about it in every direction at once: "By Emily Wunderlich, Times staff",
"emily wunderlich", "Jane Doe and John Roe", "By the Editorial Board",
"Associated Press", "newsroom@example.org". Only the first three name a
person we can give a page to.

The rule the rest of the site follows applies here too: where the data does
not support a confident answer, create nothing. A byline that resolves to
nobody is the normal case, not a failure — it is the majority of them. A
profile page for "Newsroom Staff" would be worse than no page at all, so
every test below is written to reject rather than to guess.

Resolution is three steps:

1. Split. Strip a leading "By", drop parentheticals, then cut on the
   separators feeds actually use — commas, semicolons, slashes, pipes,
   ampersands, "and", "y". Splitting on the comma does double duty: it
   separates two reporters *and* it cuts a trailing outlet credit loose
   ("Emily Wunderlich, Times staff"), which step 2 then throws away.
2. Judge each fragment on its own. Desks, editorial boards, wire services,
   job titles, email addresses and anything with a digit in it are not
   people. A rejected fragment is dropped silently; the rest of the byline
   still resolves.
3. Normalise. Fold accents, drop punctuation, lowercase. That key is the
   identity within one newsroom, so "By Emily Wunderlich, Times staff" and
   "emily wunderlich" land on the same row.

Identities are scoped to the newsroom. The same name at two newsrooms is two
rows, deliberately — see schema.sql.
"""

import re
import sys
import unicodedata
from collections import namedtuple

from .db import connect

# One person found in a byline: how to display them, what makes them the same
# person as another spelling, and the fragment of the byline they came from.
Byline = namedtuple("Byline", "name key text")

# "By", and the longer forms feeds dress it up in. The character class after
# "by" is what keeps this off a reporter actually called Byron.
_LEADING_BY = re.compile(
    r"^\s*(?:story|words|reported|written|reporting|photos?|photographs?)?\s*by\b[:\s]\s*",
    re.IGNORECASE,
)
_PARENTHETICAL = re.compile(r"\([^)]*\)")
# A quoted nickname — Ronald "DC" Reynolds — is not part of the name we file.
_NICKNAME = re.compile(r"[\"“”][^\"“”]*[\"“”]")

# Some feeds append the reporter's profile URL to the byline. Cut it before
# splitting, or the slashes in it become separators and the hostname becomes a
# person. A bare hostname has no scheme to spot, so is_person checks for one.
_URL = re.compile(r"\w+://\S+|\bwww\.\S+", re.IGNORECASE)
_LOOKS_LIKE_HOST = re.compile(r"\.[a-z]{2,}(?:$|[/?])", re.IGNORECASE)
# Likewise the contact address: "By Kendra Gilchrist kgilchrist@example.com"
# names a person, and a byline that is only an address names nobody.
_EMAIL = re.compile(r"\S+@\S+")
# Some feeds wrap the whole byline in quotes; that is not a nickname.
_WRAPPING_QUOTES = re.compile(r"^\s*[\"“”](.*)[\"“”]\s*$", re.DOTALL)

# Separators between two bylines. "and"/"y" and the dashes need surrounding
# whitespace so that a middle initial ("Jane Y. Doe") is not read as a Spanish
# conjunction and a hyphenated surname ("Clasen-Kelly") is not cut in half.
_SEPARATORS = re.compile(r"\s*[,;/|&+]\s*|\s+(?:and|y|[-–—])\s+", re.IGNORECASE)

# "Doe, Roe, and Poe" — the comma is consumed first, so the conjunction is left
# stranded at the head of the last fragment.
_LEADING_CONJUNCTION = re.compile(r"^(?:and|y)\s+", re.IGNORECASE)

# Words that make a fragment a desk, a wire, a masthead or a job title rather
# than a person. Matched against the normalised key, so these are plain words
# with spaces. The list is deliberately broad: losing a real reporter surnamed
# Post costs one missing page, while accepting "Times Staff" puts a fabricated
# person on the site.
_NOT_A_PERSON = re.compile(
    r"\b(?:"
    r"staff|newsroom|editorial|editors?|desk|bureau|team|board|"
    r"correspondents?|contributors?|contributed|columnists?|photographers?|"
    r"report|reports|reporter|reporters|reporting|writers?|"
    r"associated press|reuters|bloomberg|afp|agence france|press association|"
    r"getty|npr|pbs|upi|cnn|bbc|"
    r"news|media|magazine|journal|gazette|tribune|herald|chronicle|dispatch|"
    r"sentinel|times|post|press|daily|weekly|bulletin|observer|"
    r"radio|tv|television|public|service|network|wire|wires|syndicate|"
    r"institute|project|center|centre|foundation|association|society|coalition|"
    r"llc|inc|corp|corporation|company|group|partners|"
    r"anonymous|unknown|admin|webmaster|guest|special|submitted|"
    r"release|announcement|obituary|obituaries|"
    # Job titles, which feeds append to the name as often as they append the
    # masthead: "Dianne Nobles Ward, Tabor City Promotions Director".
    r"director|manager|publisher|producer|chief|officer|president|"
    r"coordinator|intern|fellow|analyst|host|anchor|critic|cartoonist|"
    r"illustrator|assistant|associate|senior|junior|deputy|"
    # A person's name does not join two nouns together; an organisation's does.
    r"of|for"
    r")\b"
)

# A byline never begins with an article; an organisation often does
# ("the Editorial Board", "The Conversation").
_LEADS_WITH_ARTICLE = re.compile(r"^(?:the|a|an|el|la|los|las)\b")

# Kept lowercase when we re-case an all-lowercase byline, so "van der berg"
# does not become "Van Der Berg".
_PARTICLES = {"van", "von", "de", "del", "della", "da", "di", "du", "dos",
              "das", "la", "le", "den", "der", "ter", "bin", "al", "y"}

# A person has a forename and a surname; more than five words is a sentence,
# and a twenty-character word is glued-together feed junk.
MIN_WORDS = 2
MAX_WORDS = 5
MAX_WORD_LEN = 20
MAX_SEGMENT_CHARS = 80


def _fold(text):
    """Strip accents, so José and Jose are one person and one URL."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(name):
    """The match key: what makes two spellings the same person."""
    s = _fold(name).lower().replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def slugify(name):
    """URL-safe form of a display name, for site/reporters/."""
    s = _fold(name).lower().replace("’", "'").replace("'", "")
    # Truncate first, then strip, so a cut that lands on a separator does not
    # leave the slug ending in a hyphen.
    return re.sub(r"[^a-z0-9]+", "-", s)[:60].strip("-")


def _titlecase(word):
    return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), word)


def _display(segment):
    """Tidy the casing without destroying it — McDonald and O'Brien survive."""
    words = []
    for i, word in enumerate(segment.split()):
        if i and word.lower() in _PARTICLES:
            words.append(word.lower())
        elif word.isupper() or word.islower():
            words.append(_titlecase(word))
        else:
            words.append(word)
    return " ".join(words)


def is_person(segment):
    """Does this one fragment of a byline name a person? When unsure, no."""
    if not segment or len(segment) > MAX_SEGMENT_CHARS:
        return False
    if "@" in segment or _LOOKS_LIKE_HOST.search(segment):
        return False
    key = normalize(segment)
    if not key or any(c.isdigit() for c in key):
        return False
    if _LEADS_WITH_ARTICLE.search(key) or _NOT_A_PERSON.search(key):
        return False
    words = key.split()
    if not MIN_WORDS <= len(words) <= MAX_WORDS:
        return False
    if any(len(w) > MAX_WORD_LEN for w in words):
        return False
    # A surname somewhere: "J. K. Rowling" is a person, "J. K." is not.
    return any(len(w) > 1 for w in words)


def parse_byline(author):
    """Every person named in a byline, in the order they are credited.

    Returns an empty list for a desk, a wire, a staff credit, an email
    address, or anything else we cannot confidently call a person.
    """
    if not author:
        return []
    text = _WRAPPING_QUOTES.sub(r"\1", author)
    text = _URL.sub(" ", text)
    text = _EMAIL.sub(" ", text)
    text = _NICKNAME.sub(" ", text)
    text = _PARENTHETICAL.sub(" ", text)
    text = _LEADING_BY.sub("", text)
    found, seen = [], set()
    for segment in _SEPARATORS.split(text):
        segment = (segment or "").strip().strip("-–—.")
        segment = _LEADING_CONJUNCTION.sub("", segment).strip()
        if not is_person(segment):
            continue
        key = normalize(segment)
        if key in seen:
            continue
        seen.add(key)
        found.append(Byline(_display(segment), key, segment))
    return found


def upsert_reporter(cur, org_id, byline):
    """The identity for this person at this newsroom, creating it if new.

    Returns (reporter_id, created). The slug is unique within the newsroom;
    two people whose names differ but slugify alike get a numbered suffix.
    """
    cur.execute(
        "SELECT id FROM reporters WHERE org_id = %s AND match_key = %s",
        (org_id, byline.key),
    )
    row = cur.fetchone()
    if row:
        return row[0], False

    base = slugify(byline.name) or "reporter"
    for n in range(1, 100):
        slug = base if n == 1 else f"{base}-{n}"
        cur.execute(
            "INSERT INTO reporters (org_id, name, match_key, slug) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
            (org_id, byline.name, byline.key, slug),
        )
        row = cur.fetchone()
        if row:
            return row[0], True
        # Something conflicted. If it was the match key, another run got here
        # first and that row is the answer; otherwise the slug is taken by a
        # different person and the next suffix is free.
        cur.execute(
            "SELECT id FROM reporters WHERE org_id = %s AND match_key = %s",
            (org_id, byline.key),
        )
        row = cur.fetchone()
        if row:
            return row[0], False
    raise RuntimeError(f"no free slug for {byline.name!r} at org {org_id}")


def credit(cur, article_id, org_id, author):
    """Resolve one article's byline and record the credits. Returns (people, new)."""
    people, created = 0, 0
    for byline in parse_byline(author):
        reporter_id, is_new = upsert_reporter(cur, org_id, byline)
        created += is_new
        cur.execute(
            "INSERT INTO article_reporters (article_id, reporter_id, byline_text) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (article_id, reporter_id, byline.text),
        )
        people += 1
    return people, created


def main():
    """Backfill identities from the bylines already in the database.

    Incremental by default: articles arrive with increasing ids, so the
    highest article id already credited is a safe watermark for "everything
    since last run". Bylines above the watermark that resolve to nobody are
    re-examined next run and rejected again, which is cheap and bounded to
    the recent tail. Pass --all to re-parse the whole archive, which is what
    to run after changing the rules above.
    """
    full = "--all" in sys.argv
    with connect() as conn, conn.cursor() as cur:
        since = 0
        if not full:
            cur.execute("SELECT coalesce(max(article_id), 0) FROM article_reporters")
            since = cur.fetchone()[0]

        cur.execute(
            "SELECT id, org_id, author FROM articles "
            "WHERE author IS NOT NULL AND id > %s ORDER BY id",
            (since,),
        )
        rows = cur.fetchall()
        scope = "all bylines" if full else f"bylines on articles above id {since}"
        print(f"reporters: {len(rows)} to read ({scope})")

        credits = created = resolved = 0
        for article_id, org_id, author in rows:
            people, new = credit(cur, article_id, org_id, author)
            credits += people
            created += new
            resolved += bool(people)

        cur.execute("SELECT count(*) FROM reporters")
        total = cur.fetchone()[0]
        print(f"  resolved  {resolved} bylines to {credits} credits")
        print(f"  new       {created} identities")
        print(f"  unnamed   {len(rows) - resolved} bylines named no one we could place")
        print(f"  total     {total} identities")


if __name__ == "__main__":
    main()
