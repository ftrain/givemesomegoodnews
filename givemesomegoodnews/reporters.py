"""Who wrote it: bylines resolved to people, and how much of their work we hold.

`articles.author` is whatever the feed said. On most items that is a
person's name; on the rest it is a desk, a wire service, the newsroom
itself, or whatever the CMS puts there when nobody filled the field in.
Everything that decides whether a byline names one person lives here, and
so does the one sentence the site uses to say how much somebody publishes —
a reader moving between a card and a reporter's page should never have to
wonder whether two different sentences mean the same thing.
"""

import re
import unicodedata

# A byline that carries any of these is a desk, a wire, or the CMS's idea of
# an author rather than a person. Word boundaries matter: "reporter" is part
# of plenty of real titles, but a bare "Report" is "Staff Report".
_NOT_A_PERSON = re.compile(
    r"\b(staff|newsroom|editors?|editorial|desk|admin(istrator)?|team|bureau|"
    r"contributor|guest|reader|submitted|press release|report|reports|"
    r"wire|wires|news ?service|associated press|reuters|"
    r"communications|unknown|none|anonymous)\b", re.IGNORECASE)

# Two names run together, or a name with its outlet stapled on. Either way
# the byline does not name exactly one person, so it resolves to nobody.
_SEPARATORS = re.compile(r"(,|;|/|\||\+|\band\b|&)", re.IGNORECASE)

# Two to four words, each starting with a letter. Enough for "Dana Reyes",
# "J. Ramón de la Cruz" and "Mary-Kate O'Shea"; not enough for a sentence.
_NAME = re.compile(r"^[^\W\d_][\w'’.\-]*(?: [^\W\d_][\w'’.\-]*){1,3}$")

_PUNCTUATION = re.compile(r"[^\w\s]")


def _clean(byline):
    """The byline with the furniture taken off: 'By', markup, parentheticals."""
    text = re.sub(r"<[^>]+>", " ", byline or "")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^by[\s:]+", "", text, flags=re.IGNORECASE)
    return text.strip(" .,-–—")


def reporter_name(byline):
    """The one person this byline names, or "" if it does not name one.

    Conservative on purpose. A byline the site cannot resolve to a person
    gets no profile and no marker beside it — an empty panel, or a panel
    holding a whole newsroom's output under "Staff Report", would be worse
    than leaving the byline as the plain line of text it has always been.
    """
    text = _clean(byline)
    if not text or len(text) > 60 or "@" in text:
        return ""
    if _SEPARATORS.search(text) or _NOT_A_PERSON.search(text):
        return ""
    return text if _NAME.match(text) else ""


def reporter_key(byline):
    """Identity: one person written two ways is one reporter.

    Feeds disagree about capitals, initials and periods, so "By DANA REYES"
    and "Dana Reyes" have to land on the same key or the same person gets
    counted twice.
    """
    name = reporter_name(byline)
    if not name:
        return ""
    folded = _PUNCTUATION.sub("", name.replace("’", "'").casefold())
    return re.sub(r"\s+", " ", folded).strip()


def reporter_slug(byline):
    """The reporter's page under `reporters/`, as a filename stem."""
    key = reporter_key(byline)
    if not key:
        return ""
    ascii_key = (unicodedata.normalize("NFKD", key)
                 .encode("ascii", "ignore").decode("ascii"))
    return re.sub(r"[^a-z0-9]+", "-", ascii_key).strip("-")


def prolificacy(n_stories):
    """How much of a reporter's work this site holds, as one sentence.

    The disclosure beside a byline and the reporter's own page both say
    this, and they say it with the same call rather than with two sentences
    that have to be kept in step by hand.
    """
    if n_stories <= 0:
        return "Nothing here under this byline yet."
    if n_stories == 1:
        return "One story on this site."
    if n_stories < 5:
        return f"{n_stories} stories on this site."
    if n_stories < 15:
        return f"{n_stories} stories on this site — a regular byline."
    return f"{n_stories} stories on this site — one of its most prolific."
