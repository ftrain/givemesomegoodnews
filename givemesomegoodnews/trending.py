"""What local newsrooms are covering more than usual today.

Once an hour this compares the last day of stories with the two weeks before
it and writes a snapshot, which build_site renders as trending.html.

Finding the topics is counting, and no language model is involved:

1. Stories are read the way the front page reads them — English, the default
   newsrooms, the feed filters applied — and reprints fold into one story,
   so a wire piece carried by eleven papers is one story, credited to the
   newsroom that ran it first.
2. Each headline is broken into words and two-word phrases. A term's count
   is the number of newsrooms that originated a story using it; today's
   count is set against its daily average over the baseline, and scored by
   how far above that average it sits: (today - expected) / sqrt(expected + 1).
3. A term needs MIN_NEWSROOMS newsrooms in MIN_STATES states, and at least
   MIN_RATIO times its usual count (MIN_RATIO_WORD for a single word).
4. Terms that describe mostly the same stories merge into one topic, and
   topics whose stories are close by embedding merge after that: one event
   is headlined in many different words.

Naming is the one place a model is used. The headlines of the strongest
candidates — headlines only, never summaries — go to DeepSeek, which labels
each topic, says which candidates are the same topic, and rejects the ones
that are a coincidence of vocabulary rather than a topic. It cannot add a
story or change a count: every number on the page is computed here, and
anything it returns that does not name something it was given is ignored.
Without DEEPSEEK_API_KEY, or when the call fails, a topic is named by its
strongest phrase, as it appears in the headlines.
"""

import argparse
import collections
import hashlib
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

from . import filters, language
from .build_site import collapse_duplicates, title_tokens
from .db import connect

WINDOW_HOURS = int(os.environ.get("TRENDING_WINDOW_HOURS", "24"))
BASELINE_DAYS = int(os.environ.get("TRENDING_BASELINE_DAYS", "14"))
MIN_NEWSROOMS = int(os.environ.get("TRENDING_MIN_NEWSROOMS", "3"))
MIN_STATES = int(os.environ.get("TRENDING_MIN_STATES", "2"))
MIN_RATIO = float(os.environ.get("TRENDING_MIN_RATIO", "3"))
# A single word needs a bigger jump than a phrase: "man" and "court" drift
# with the day's mix of stories, "hurricane erin" does not.
MIN_RATIO_WORD = float(os.environ.get("TRENDING_MIN_RATIO_WORD", "5"))
MIN_SCORE = float(os.environ.get("TRENDING_MIN_SCORE", "2.5"))
# Two terms are one topic when this share of the smaller one's stories are
# also the other's, and the smaller is at least MERGE_SIZE of the larger: a
# story about mail ballots is a Trump story, but it is not the Trump topic.
# A term nearly contained in another (MERGE_CONTAINED) merges whatever its size.
MERGE_OVERLAP = 0.5
MERGE_SIZE = 0.25
MERGE_CONTAINED = 0.8
# Topics whose own stories — the ones they do not share — sit this close by
# embedding are one event told in different words: "25th anniversary",
# "remembers" and "first responders" on September 11th. Tried against a week
# of the archive, 0.25 also joined "25th anniversary" to "4th annual" and a
# convention speech to a congressional map ruling; 0.29 lost the speech and
# its $5,000 dividend pledge, which are one story. What 0.27 leaves apart —
# "Ground zero" beside the anniversary — is the naming step's to merge.
MERGE_SIMILAR = float(os.environ.get("TRENDING_MERGE_SIM", "0.27"))
# Two topics naming the same number merge at this lower bar. Headline style
# spells out a number that opens a headline, so one anniversary arrives as
# "Twenty-five years later" and as "25th anniversary" — 0.26 apart, which
# is also how far "25th anniversary" is from an unrelated "4th annual".
# The number is what tells them apart.
MERGE_SIMILAR_SAME_NUMBER = float(os.environ.get("TRENDING_MERGE_SIM_NUMBER", "0.2"))
# How many word-built topics go into the embedding merge; merging frees
# candidate places, so it takes more than it will offer for naming.
MERGE_POOL = 40
# Candidates offered for naming, and topics the page shows.
CANDIDATES = 20
TOPICS_SHOWN = 12
# Headlines per candidate sent for naming.
HEADLINES_PER_CANDIDATE = 25
LABEL_MAX = 80

DEEPSEEK_URL = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
DEEPSEEK_TIMEOUT = 60

# Words that rise and fall with the calendar, not the news: every Tuesday
# "tuesday" is five times as common as it was on average.
CALENDAR_WORDS = frozenset(
    """monday tuesday wednesday thursday friday saturday sunday
    january february march april may june july august september october
    november december jan feb mar apr jun jul aug sep sept oct nov dec
    today tonight yesterday tomorrow weekend week weeks day days month
    months year years morning afternoon evening""".split()
)
# Headline filler: common in every day's headlines, never the subject of one.
FILLER_WORDS = frozenset(
    """yet still just back know need needs make makes made get gets got take
    takes one two three first last next ahead amid near across announces
    here way ways look looks things""".split()
)
# English stories still carry Spanish and French names and phrases; their
# function words are never a topic.
FOREIGN_STOPWORDS = frozenset().union(
    *(words for name, words in language.STOPWORDS.items() if name != "English"))


# --- terms -----------------------------------------------------------------

def headline_words(title):
    """Content words in headline order: title_tokens' normalization, minus
    numbers, calendar words and filler."""
    # Soft hyphens split "uphold\u00ads" into two words for anyone but a reader.
    title = title.replace("\u00ad", "")
    kept = title_tokens(title)
    norm = title.lower().replace("’", "'").replace("‘", "'")
    out = []
    for raw in re.findall(r"[a-z0-9']+", norm):
        w = re.sub(r"'s$", "", raw).replace("'", "")
        if (w in kept and not w.isdigit() and w not in CALENDAR_WORDS
                and w not in FILLER_WORDS and w not in FOREIGN_STOPWORDS):
            out.append(w)
    return out


ORDINAL = re.compile(r"^\d+(st|nd|rd|th)$")


def terms_of(title):
    """Every word and adjacent pair of words in a headline, as a set.

    An ordinal counts only inside a phrase: "25th anniversary" is a topic,
    "5th" matches every Fifth District race in the country.
    """
    words = headline_words(title)
    pairs = {f"{a} {b}" for a, b in zip(words, words[1:]) if a != b}
    return {w for w in words if not ORDINAL.match(w)} | pairs


def fold_stories(articles):
    """Group reprints into stories, oldest first.

    Each story is a dict with its 'lead' (the earliest copy, whose newsroom
    is credited with it), every 'copies', and its headline 'terms'.
    """
    ordered = sorted(articles, key=lambda a: (a["at"], a["id"]))
    stories = []
    for entry in collapse_duplicates(ordered):
        copies = [entry] + entry["_also"]
        stories.append({
            "lead": entry,
            "copies": copies,
            "terms": terms_of(entry["title"]),
        })
    return stories


def baseline_counts(articles, wanted, days, scale_to=None):
    """Average newsrooms a day originating a story with each wanted term.

    The baseline is too long to fold fuzzily, so reprints fold on identical
    headline words; a retitled reprint counts twice, which only makes the
    baseline stricter.

    With scale_to — the number of newsrooms publishing today — the averages
    are scaled by how many newsrooms publish on an ordinary baseline day, so
    a Sunday, a crawl outage or a batch of newly added feeds does not read
    as every word trending at once.
    """
    seen = set()
    pairs = collections.defaultdict(set)
    active = set()
    for a in sorted(articles, key=lambda a: (a["at"], a["id"])):
        key = frozenset(title_tokens(a["title"]))
        if key in seen:
            continue
        seen.add(key)
        day = a["at"].date()
        active.add((a["org_id"], day))
        for term in terms_of(a["title"]) & wanted:
            pairs[term].add((a["org_id"], day))
    factor = 1.0
    if scale_to and active:
        factor = scale_to / (len(active) / days)
    return {term: factor * len(p) / days for term, p in pairs.items()}


def score(today, expected):
    return (today - expected) / math.sqrt(expected + 1)


def rising_terms(stories, expected):
    """Terms well above their usual count, strongest first."""
    by_term = collections.defaultdict(list)
    for i, story in enumerate(stories):
        for term in story["terms"]:
            by_term[term].append(i)
    rising = []
    for term, idx in by_term.items():
        orgs = {stories[i]["lead"]["org_id"] for i in idx}
        states = {stories[i]["lead"]["state"] for i in idx if stories[i]["lead"]["state"]}
        if len(orgs) < MIN_NEWSROOMS or len(states) < MIN_STATES:
            continue
        e = expected.get(term, 0.0)
        if len(orgs) < (MIN_RATIO if " " in term else MIN_RATIO_WORD) * e:
            continue
        s = score(len(orgs), e)
        if s < MIN_SCORE:
            continue
        rising.append({"term": term, "stories": set(idx), "score": s,
                       "newsrooms": len(orgs), "expected": e})
    # A phrase before its own words at an equal score, then alphabetically,
    # so the same data makes the same list.
    rising.sort(key=lambda t: (-t["score"], -t["term"].count(" "), t["term"]))
    return rising


def matches(terms, story_terms):
    """How many of a topic's terms a story carries, counting a word inside a
    matched phrase once: "supreme court" is one match, not two."""
    found = set(terms) & story_terms
    phrases = [t for t in found if " " in t]
    return sum(1 for t in found if " " in t or not any(t in p.split() for p in phrases))


def group_terms(rising, stories):
    """Merge terms that describe mostly the same stories into topics.

    A topic keeps the stories carrying its strongest term, plus any story
    carrying two of its terms: a merged "heat" should not bring in every
    story about heat pumps.
    """
    topics = []
    for t in rising:
        home = None
        for topic in topics:
            seed = topic["seed_stories"]
            small, large = sorted((len(t["stories"]), len(seed)))
            overlap = len(t["stories"] & seed) / small
            if overlap >= MERGE_CONTAINED or (
                    overlap >= MERGE_OVERLAP and small >= MERGE_SIZE * large):
                home = topic
                break
        if home:
            home["terms"].append(t["term"])
        else:
            topics.append({"terms": [t["term"]], "seed_stories": t["stories"],
                           "score": t["score"]})
    for topic in topics:
        topic["stories"] = {
            i for i, s in enumerate(stories)
            if topic["terms"][0] in s["terms"] or matches(topic["terms"], s["terms"]) >= 2
        }
    return topics


_UNITS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_UNIT_ORDINALS = {w: n for n, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
    "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
    "nineteenth".split())}
_TENS = {w: 10 * n for n, w in enumerate(
    "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_TENS_ORDINALS = {w[:-1] + "ieth": n for w, n in _TENS.items()}


def topic_numbers(terms):
    """The numbers a topic's terms name, however written: "25th anniversary"
    and "twenty five" both name 25. Only 10 to 999: single digits and years
    turn up in every day's unrelated headlines."""
    found = set()
    for term in terms:
        words = term.split()
        for k, w in enumerate(words):
            digits = re.match(r"^(\d+)(st|nd|rd|th)?$", w)
            if digits:
                found.add(int(digits.group(1)))
            elif w in _TENS or w in _TENS_ORDINALS:
                n = _TENS.get(w) or _TENS_ORDINALS[w]
                nxt = words[k + 1] if k + 1 < len(words) else ""
                unit = _UNITS.get(nxt, _UNIT_ORDINALS.get(nxt))
                found.add(n + unit if unit is not None and 0 < unit < 10 else n)
            elif w in _UNITS or w in _UNIT_ORDINALS:
                found.add(_UNITS.get(w, _UNIT_ORDINALS.get(w)))
    return {n for n in found if 10 <= n <= 999}


def _add(acc, vec, sign=1.0):
    for k, x in enumerate(vec):
        acc[k] += sign * x


def _unit_cosine(a, b):
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def merge_similar(topics, stories, threshold=MERGE_SIMILAR,
                  same_number=MERGE_SIMILAR_SAME_NUMBER):
    """Merge topics that are one event told in different words.

    Repeatedly joins the closest pair of topics while they are at least
    `threshold` apart by cosine, comparing each topic's *own* stories — the
    ones the other does not share. A single headline that happens to carry
    both "Planned Parenthood" and "general election" would otherwise pull two
    unrelated small topics together. A topic wholly inside another merges.
    Topics naming the same number (see topic_numbers) need only
    MERGE_SIMILAR_SAME_NUMBER. Each story's unit embedding is its "vector";
    stories without one are left out of the comparison, not out of the topic.
    """
    clusters = [{"terms": list(t["terms"]), "score": t["score"], "stories": set(t["stories"])}
                for t in topics]
    dim = next((len(s["vector"]) for s in stories if s.get("vector")), 0)

    def total(ids):
        acc = [0.0] * dim
        for i in ids:
            if stories[i].get("vector"):
                _add(acc, stories[i]["vector"])
        return acc

    def similarity(a, b):
        """How close two topics are, as a margin over the bar they must clear:
        0 or more merges."""
        bar = same_number if topic_numbers(a["terms"]) & topic_numbers(b["terms"]) else threshold
        return cosine(a, b) - bar

    def cosine(a, b):
        shared = a["stories"] & b["stories"]
        if shared == a["stories"] or shared == b["stories"]:
            return 1.0
        if not dim:
            return 0.0
        own_a, own_b = list(a["sum"]), list(b["sum"])
        if shared:
            common = total(shared)
            _add(own_a, common, -1.0)
            _add(own_b, common, -1.0)
        return _unit_cosine(own_a, own_b)

    for c in clusters:
        c["sum"] = total(c["stories"])
    sims = {(a, b): similarity(clusters[a], clusters[b])
            for a in range(len(clusters)) for b in range(a + 1, len(clusters))}
    alive = set(range(len(clusters)))
    while sims:
        (a, b), best = max(sims.items(), key=lambda kv: (kv[1], -kv[0][0], -kv[0][1]))
        if best < 0:
            break
        # The stronger topic leads, so its terms name the merged one.
        keep, drop = (a, b) if clusters[a]["score"] >= clusters[b]["score"] else (b, a)
        k, d = clusters[keep], clusters[drop]
        k["terms"] += [t for t in d["terms"] if t not in k["terms"]]
        k["score"] = max(k["score"], d["score"])
        k["stories"] |= d["stories"]
        k["sum"] = total(k["stories"])
        alive.discard(drop)
        sims = {pair: s for pair, s in sims.items() if keep not in pair and drop not in pair}
        for other in alive - {keep}:
            pair = (min(keep, other), max(keep, other))
            sims[pair] = similarity(clusters[pair[0]], clusters[pair[1]])
    merged = [clusters[i] for i in sorted(alive)]
    for c in merged:
        del c["sum"]
    merged.sort(key=lambda c: -c["score"])
    return merged


def measure(story_ids, stories):
    """The counts the page prints, from the stories themselves."""
    chosen = [stories[i] for i in story_ids]
    leads = {s["lead"]["org_id"] for s in chosen}
    lead_states = {s["lead"]["state"] for s in chosen if s["lead"]["state"]}
    copies = [c for s in chosen for c in s["copies"]]
    return {
        "n_stories": len(chosen),
        "n_newsrooms": len({c["org_id"] for c in copies}),
        "n_states": len({c["state"] for c in copies if c["state"]}),
        "lead_newsrooms": len(leads),
        "lead_states": len(lead_states),
    }


def qualifies(counts):
    return counts["lead_newsrooms"] >= MIN_NEWSROOMS and counts["lead_states"] >= MIN_STATES


def strongest_phrase(terms):
    """The best-scoring two-word term, else the best word: "mail voting"
    names a topic, "mail" only hints at it."""
    return next((t for t in terms if " " in t), terms[0])


def surface_form(term, titles):
    """The term as the headlines actually write it: "FEMA", "Hurricane Erin".

    Title-case headlines capitalize everything, so when any headline writes
    the phrase with a lowercase word after the first, that spelling wins:
    "school shooting" is not a name, "Colorado River" is.
    """
    words = term.split()
    # Terms skip numbers, stopwords and calendar words, so a phrase can have
    # a few of those between its words in the headline: "marks 25 years since".
    pattern = r"[\s\-–—:,]+(?:[\w'’]+[\s\-–—:,]+){0,3}?".join(
        re.escape(w) + r"(?:['’]s)?" for w in words)
    found = collections.Counter()
    for title in titles:
        for m in re.finditer(r"\b" + pattern + r"\b", title, re.IGNORECASE):
            found[m.group(0)] += 1
    if not found:
        return term[0].upper() + term[1:]
    plain = {t: n for t, n in found.items() if any(w[0].islower() for w in t.split()[1:])}
    text = max((plain or found).items(), key=lambda kv: (kv[1], kv[0]))[0]
    text = re.sub(r"['’]s\b", "", text)
    return text[0].upper() + text[1:]


# --- naming ------------------------------------------------------------------

SYSTEM_PROMPT = """You name trending topics for a website that collects local news \
from about a thousand independent newsrooms across the United States. A program has \
already found groups of headlines that share words far more often today than usual. \
For each group, decide whether it is one real news topic, and if it is, name it.

Rules:
- A label is 2 to 6 words, sentence case, plain and neutral, the way a newspaper's \
index would put it: "Hurricane Erin reaches the Carolinas", "School cellphone bans", \
"Medicaid work requirements". No opinion, no emoji, no trailing punctuation.
- Use only what the headlines say. Add no facts, numbers or names that are not in them.
- If two or more groups are the same topic, put them in one topic.
- Reject a group that is not a topic: headlines that merely share a common word or \
phrase but are about unrelated things, press releases or advertising copy, calendar \
or event listings, routine forecasts, or site boilerplate.
- If a group is a topic but some of its headlines are about something else, list \
those headline ids in "exclude".
- When a label from the previous hour still fits a topic, reuse it word for word.

Reply with json only, in exactly this shape:
{"topics": [{"groups": ["g1", "g4"], "label": "School cellphone bans", "exclude": [123]}], \
"rejected": ["g2"]}
Every group id you were given appears exactly once, in one topic or in rejected."""


def naming_request(candidates, stories, previous_labels):
    groups = []
    for n, topic in enumerate(candidates, 1):
        chosen = sorted(topic["stories"], key=lambda i: stories[i]["lead"]["at"], reverse=True)
        groups.append({
            "id": f"g{n}",
            "shared_words": topic["terms"][:5],
            "headlines": [{"id": stories[i]["lead"]["id"], "title": stories[i]["lead"]["title"]}
                          for i in chosen[:HEADLINES_PER_CANDIDATE]],
        })
    return {"previous_labels": previous_labels, "groups": groups}


def ask_deepseek(payload, api_key):
    """The model's decision as a dict, or None. Never raises."""
    body = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": 0.2,
        "max_tokens": 3000,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # The API occasionally answers with empty content; one retry covers it.
    for attempt in (1, 2):
        try:
            r = requests.post(DEEPSEEK_URL, json=body, headers=headers, timeout=DEEPSEEK_TIMEOUT)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
            if content.strip():
                decision = json.loads(content)
                if isinstance(decision, dict):
                    return decision
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as e:
            # The exception text, never the request: that carries the key.
            print(f"trending: naming attempt {attempt} failed: {type(e).__name__}: "
                  f"{str(e)[:200]}", file=sys.stderr)
    return None


def clean_label(value):
    if not isinstance(value, str):
        return None
    label = re.sub(r"\s+", " ", value).strip().strip(".;:,")
    if not label or len(label) > LABEL_MAX or "<" in label:
        return None
    return label


def apply_decision(candidates, stories, decision):
    """Topics from the candidates and the model's decision.

    The decision can only choose among what it was given: unknown group ids,
    reused groups, and excluded headlines that were not in the group are
    ignored. With no decision at all, the candidates built around a phrase
    stand under their own names — a lone word is too often a coincidence to
    show unchecked; with a decision, a group it neither placed nor rejected is left out, as
    the model's silence is likelier a rejection than an endorsement. Counts
    are measured afterwards, from the stories that remain.
    """
    if not isinstance(decision, dict):
        return [{"label": None, "terms": list(t["terms"]), "score": t["score"],
                 "stories": set(t["stories"])}
                for t in candidates if any(" " in term for term in t["terms"])]
    by_id = {f"g{n}": topic for n, topic in enumerate(candidates, 1)}
    lead_index = {s["lead"]["id"]: i for i, s in enumerate(stories)}
    used = set()
    topics = []

    raw_topics = decision.get("topics")
    for raw in raw_topics if isinstance(raw_topics, list) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list):
            continue
        members = []
        for gid in raw["groups"]:
            if isinstance(gid, str) and gid in by_id and gid not in used:
                used.add(gid)
                members.append(by_id[gid])
        if not members:
            continue
        story_ids = set().union(*(m["stories"] for m in members))
        exclude = raw.get("exclude")
        for aid in exclude if isinstance(exclude, list) else []:
            if isinstance(aid, int) and lead_index.get(aid) in story_ids:
                story_ids.discard(lead_index[aid])
        topics.append({
            "label": clean_label(raw.get("label")),
            "terms": [t for m in members for t in m["terms"]],
            "score": max(m["score"] for m in members),
            "stories": story_ids,
        })
    return topics


def finish(topics, stories, limit=TOPICS_SHOWN):
    """Measure, drop what no longer qualifies, name what is unnamed, rank."""
    out = []
    for topic in topics:
        counts = measure(topic["stories"], stories)
        if not qualifies(counts):
            continue
        titles = [c["title"] for i in topic["stories"] for c in stories[i]["copies"]]
        phrase = surface_form(strongest_phrase(topic["terms"]), titles)
        label = topic["label"] or phrase
        # Most central first — the stories carrying most of the topic's terms,
        # then the most carried — each followed by its reprints.
        ranked = sorted(topic["stories"], key=lambda i: (
            -matches(topic["terms"], stories[i]["terms"]),
            -len(stories[i]["copies"]), -stories[i]["lead"]["at"].timestamp(),
            stories[i]["lead"]["id"]))
        article_ids = [c["id"] for i in ranked for c in stories[i]["copies"]]
        out.append({**counts, "label": label, "terms": topic["terms"],
                    "score": round(topic["score"], 3), "article_ids": article_ids,
                    "search": phrase})
    out.sort(key=lambda t: (-t["score"], -t["n_newsrooms"], t["label"]))
    return out[:limit]


def fingerprint(candidates, stories):
    shape = [[t["terms"], sorted(stories[i]["lead"]["id"] for i in t["stories"])]
             for t in candidates]
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()


# --- database ----------------------------------------------------------------

def load_stories(cur, start, end):
    filter_sql, params = filters.where_clause(cur)
    # A published date in the future is a feed's mistake; the fetch time
    # is the latest a story can really have run.
    at = "least(coalesce(a.published_at, a.fetched_at), a.fetched_at)"
    cur.execute(
        f"""
        SELECT a.id, a.title, {at} AS at, o.id, o.state, a.url
        FROM articles a JOIN orgs o ON o.id = a.org_id
        WHERE {at} > %s AND {at} <= %s
          AND o.in_default
          AND coalesce(a.language, 'English') = 'English'
          {("AND " + filter_sql) if filter_sql else ""}
        """,
        [start, end, *params],
    )
    return [dict(zip(("id", "title", "at", "org_id", "state", "url"), row))
            for row in cur.fetchall()]


def load_vectors(cur, stories):
    """Give each story its lead's unit embedding as "vector", where it has one."""
    ids = [s["lead"]["id"] for s in stories]
    cur.execute("SELECT id, embedding FROM articles WHERE id = ANY(%s) AND embedding IS NOT NULL",
                (ids,))
    found = {}
    for article_id, vec in cur.fetchall():
        vec = json.loads(vec) if isinstance(vec, str) else list(vec)
        norm = math.sqrt(sum(x * x for x in vec))
        if norm:
            found[article_id] = [x / norm for x in vec]
    for s in stories:
        s["vector"] = found.get(s["lead"]["id"])


def latest_snapshot(cur):
    cur.execute(
        "SELECT id, fingerprint, labeler, decision FROM trending_snapshots "
        "ORDER BY generated_at DESC, id DESC LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    snap = dict(zip(("id", "fingerprint", "labeler", "decision"), row))
    cur.execute("SELECT label FROM trending_topics WHERE snapshot_id = %s ORDER BY rank",
                (snap["id"],))
    snap["labels"] = [r[0] for r in cur.fetchall()]
    return snap


def save_snapshot(cur, conn, end, fp, labeler, decision, topics):
    with conn.transaction():
        cur.execute(
            "INSERT INTO trending_snapshots (window_end, window_hours, baseline_days, "
            "fingerprint, labeler, decision) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (end, WINDOW_HOURS, BASELINE_DAYS, fp, labeler,
             json.dumps(decision) if decision is not None else None),
        )
        snap_id = cur.fetchone()[0]
        for rank, t in enumerate(topics, 1):
            cur.execute(
                "INSERT INTO trending_topics (snapshot_id, rank, label, terms, search, score, "
                "n_stories, n_newsrooms, n_states, article_ids) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (snap_id, rank, t["label"], t["terms"], t["search"], t["score"],
                 t["n_stories"], t["n_newsrooms"], t["n_states"], t["article_ids"]),
            )
    return snap_id


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print the topics, write nothing")
    ap.add_argument("--no-label", action="store_true", help="never call the naming model")
    ap.add_argument("--at", help="end the window here (ISO time, or 'latest' for the "
                                 "newest story in the database) instead of now")
    args = ap.parse_args(argv)

    with connect() as conn, conn.cursor() as cur:
        if args.at == "latest":
            cur.execute("SELECT max(least(coalesce(published_at, fetched_at), fetched_at)) "
                        "FROM articles")
            end = cur.fetchone()[0] or datetime.now(timezone.utc)
        elif args.at:
            end = datetime.fromisoformat(args.at)
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
        else:
            end = datetime.now(timezone.utc)
        start = end - timedelta(hours=WINDOW_HOURS)

        stories = fold_stories(load_stories(cur, start, end))
        wanted = set().union(*(s["terms"] for s in stories)) if stories else set()
        cur.execute("SELECT min(least(coalesce(published_at, fetched_at), fetched_at)) "
                    "FROM articles")
        oldest = cur.fetchone()[0]
        days = BASELINE_DAYS
        if oldest:
            # A young database has less history than the baseline asks for.
            days = max(1.0, min(days, (start - oldest).total_seconds() / 86400))
        publishing = len({s["lead"]["org_id"] for s in stories})
        expected = baseline_counts(
            load_stories(cur, start - timedelta(days=days), start), wanted, days,
            scale_to=publishing)

        rising = rising_terms(stories, expected)
        load_vectors(cur, stories)
        candidates = merge_similar(group_terms(rising, stories)[:MERGE_POOL], stories)[:CANDIDATES]
        fp = fingerprint(candidates, stories)
        print(f"trending: {len(stories)} stories in the {WINDOW_HOURS}h to "
              f"{end:%Y-%m-%d %H:%M %Z}, baseline {days:.1f} days; "
              f"{len(rising)} rising terms, {len(candidates)} candidates")

        previous = latest_snapshot(cur)
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        decision, labeler = None, "terms"
        if not candidates:
            pass
        elif previous and previous["fingerprint"] == fp and previous["decision"] is not None:
            decision, labeler = previous["decision"], previous["labeler"]
            print("trending: candidates unchanged since the last snapshot; reusing its names")
        elif api_key and not args.no_label:
            decision = ask_deepseek(
                naming_request(candidates, stories, previous["labels"] if previous else []),
                api_key)
            if decision is not None:
                labeler = DEEPSEEK_MODEL

        topics = finish(apply_decision(candidates, stories, decision), stories)
        for rank, t in enumerate(topics, 1):
            print(f"  {rank:2d}. {t['label']:<44} {t['score']:6.2f}  "
                  f"{t['n_stories']:3d} stories {t['n_newsrooms']:3d} newsrooms "
                  f"{t['n_states']:2d} states  [{', '.join(t['terms'][:4])}]")
        if args.dry_run:
            if decision is not None:
                print(json.dumps(decision, indent=1))
            return
        snap_id = save_snapshot(cur, conn, end, fp, labeler, decision, topics)
        print(f"trending: snapshot {snap_id}, {len(topics)} topics, named by {labeler}")


if __name__ == "__main__":
    main()
