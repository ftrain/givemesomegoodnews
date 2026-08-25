"""Subject tagging, without a language model.

Three layers, most trustworthy first:

1. **declared** — the publisher's own RSS categories, mapped onto our small
   taxonomy through a whitelist. About four in five entries carry
   categories; the whitelist is what separates the real subjects
   (``education``, ``sports``) from place names (``jefferson county``) and
   CMS furniture (``home: lead``, ``uncategorized``), which are ignored by
   simply not appearing in it.
2. **url** — path segments like ``/sports/`` or ``/opinion/``, for the
   newsrooms whose feeds declare nothing.
3. **vector** — see :mod:`givemesomegoodnews.classify`. Articles labeled by
   layers 1 and 2 are averaged into one prototype vector per subject, and
   whatever is left is assigned its nearest prototype. This is what the
   hashing embedder can genuinely do: subjects share vocabulary, and a
   centroid of a few hundred sports stories is dense in that vocabulary.
   Matching against the bare *word* "Sports" would not work at all.
"""

SUBJECTS = [
    "News", "Politics", "Education", "Health", "Environment",
    "Housing", "Business", "Arts", "Sports", "Food", "Opinion",
]

# Publisher category terms → subject. A whitelist on purpose: an unknown
# term (usually a place or a CMS flag) contributes nothing rather than
# guessing. Terms are matched lowercased; substrings count, so
# "housing and homelessness" hits "housing".
TERM_MAP = {
    "Politics": [
        "politics", "government", "government and public institutions", "election",
        "elections", "city hall", "city council", "state government", "legislature",
        "policy", "public safety", "criminal justice", "courts", "immigration",
        "police", "voting", "campaign",
    ],
    "Education": ["education", "schools", "school", "students", "higher ed", "university", "k-12"],
    "Health": ["health", "healthcare", "health and safety", "public health", "mental health", "medicine", "hospital"],
    "Environment": ["environment", "climate", "energy", "water", "wildlife", "conservation", "pollution", "wildfire"],
    "Housing": ["housing", "homelessness", "real estate", "development", "zoning", "eviction", "rent", "nycha"],
    "Business": ["business", "economy", "economic", "labor", "work", "jobs", "tech", "technology", "transportation", "transit"],
    "Arts": [
        "arts", "art", "culture", "entertainment", "music", "film", "movies", "theater",
        "books", "literature", "museum", "video games", "games", "media", "tv",
    ],
    "Sports": ["sports", "sport", "football", "basketball", "baseball", "soccer", "hockey", "athletics", "college sports"],
    "Food": ["food", "dining", "restaurants", "restaurant", "drink", "eats", "recipes"],
    "Opinion": ["opinion", "op-ed", "oped", "editorial", "commentary", "viewpoints", "perspectives", "letters", "column"],
    "News": ["news", "local news", "breaking news", "community news"],
}

# URL path segments → subject, for feeds that declare no categories.
PATH_MAP = {
    "Opinion": ["opinion", "opinions", "editorial", "editorials", "commentary", "op-ed", "viewpoints", "columns"],
    "Sports": ["sports", "sport", "athletics"],
    "Arts": ["arts", "art", "culture", "entertainment", "music", "film", "books", "theater", "things-to-do"],
    "Food": ["food", "dining", "restaurants", "eat", "eats", "drink"],
    "Politics": ["politics", "government", "elections", "election", "courts", "crime", "public-safety", "immigration"],
    "Education": ["education", "schools", "school"],
    "Health": ["health", "healthcare", "public-health"],
    "Environment": ["environment", "climate", "energy", "water"],
    "Housing": ["housing", "real-estate", "development"],
    "Business": ["business", "economy", "jobs", "labor", "tech", "transportation", "transit"],
    "News": ["news", "local"],
}

# "News" is the fallback bucket: only take it when nothing more specific fits.
_SPECIFIC = [s for s in SUBJECTS if s != "News"]


def _score(haystack_terms, table):
    """Score each subject by how many of its terms appear. Specific wins."""
    scores = {}
    for subject in list(_SPECIFIC) + ["News"]:
        hits = 0
        for term in table.get(subject, []):
            for raw in haystack_terms:
                if term == raw or (len(term) > 4 and term in raw):
                    hits += 1
                    break
        if hits:
            scores[subject] = hits
    if not scores:
        return None
    specific = {s: n for s, n in scores.items() if s != "News"}
    pool = specific or scores
    return max(pool, key=lambda s: (pool[s], -SUBJECTS.index(s)))


def subject_from_terms(terms):
    """Map a publisher's declared categories onto one subject, or None."""
    cleaned = [t.strip().lower() for t in (terms or []) if t and t.strip()]
    return _score(cleaned, TERM_MAP) if cleaned else None


def subject_from_url(url):
    """Read a subject out of the URL's path segments, or None."""
    if not url:
        return None
    try:
        path = url.split("//", 1)[-1].split("/", 1)[1]
    except IndexError:
        return None
    segments = [s.lower() for s in path.split("?")[0].split("/") if s and not s.isdigit()]
    # Only whole path segments count — "sports" as a segment, not as part of
    # a headline slug like "city-sports-complex-vote".
    for subject in list(_SPECIFIC) + ["News"]:
        if any(seg in PATH_MAP.get(subject, []) for seg in segments):
            return subject
    return None


def classify(categories, url):
    """Best non-vector guess. Returns (subject, source) or (None, None)."""
    subject = subject_from_terms(categories)
    if subject:
        return subject, "declared"
    subject = subject_from_url(url)
    if subject:
        return subject, "url"
    return None, None
