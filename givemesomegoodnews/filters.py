"""What the feed leaves out.

Obituaries, death notices and horoscopes are a large share of what small
papers publish and are not what anyone comes here to read. They stay in the
database and stay searchable — they are simply kept off the feed pages.

Patterns are POSIX regexes matched case-insensitively, and live in the
feed_filters table so they can be changed in the admin tool without a
deploy. Word boundaries (\\y) matter: a bare "obit" would also catch
nothing useful, but an unanchored "memoriam" could catch a genuine story
about a memorial.
"""

from .db import connect

FIELDS = ("title", "summary", "url", "subject")

# Seeded once into an empty table; after that the admin tool owns them.
DEFAULTS = [
    ("title", r"\yobit(uary|uaries|s)?\y", "obituaries"),
    ("url", r"/obitu?ar|/obits?/", "obituary sections"),
    ("title", r"\ydeath notices?\y", "death notices"),
    ("title", r"\yin memoriam\y", "memorial notices"),
    ("title", r"\yfuneral (home|notice|arrangements)", "funeral notices"),
    ("title", r"\yhoroscopes?\y", "horoscopes"),
    ("title", r"\ylottery (numbers|results)\y", "lottery results"),
    ("title", r"\yrecipes?\y|\yhow to make\y", "recipes"),
    ("title", r"\ythings to do\y|\yweekend guide\y|\yevents? (calendar|listing)", "listings"),
    ("title", r"\ytraffic (report|alert)\y|\yroad (closures?|work) (report|week)|"
              r"\yongoing traffic\y", "traffic notices"),
    ("title", r"\ynews quiz\y|\ycrossword\y|\ysudoku\y", "puzzles"),
    ("title", r"\y(certification|training) course\y|\yevent announcement\y|"
              r"\yregistration (is )?open\y", "event announcements"),
    ("url", r"/events?/|/calendar/|/classifieds?/|/recipes?/", "events and classifieds"),
    # Not civic reporting. Each still has its own subject page and feed.
    ("subject", r"^(Food|Sports|Arts)$", "lifestyle subjects"),
    ("title", r"\ycomics?\y|\ycartoons?\y|\ycomic strip\y", "comics"),
    ("title", r"^\w*day.?s headlines\y|^headlines\y|\ymorning (roundup|briefing)\y|"
              r"\ywhat to know (today|this week)\y", "roundups"),
    ("title", r"\ylive\y.*\y(webcam|cam|aurora|borealis|stream)\y", "livestreams"),
    ("title", r"\yphotos? of the (day|week)\y|\yphoto gallery\y", "photo galleries"),
    ("title", r"\yon a (roll|bun)\y|\yin a bowl\y", "recipe columns"),
]


def seed_defaults(cur):
    """Put the starting set in place, once, if nobody has set any."""
    cur.execute("SELECT count(*) FROM feed_filters")
    if cur.fetchone()[0]:
        return 0
    for field, pattern, note in DEFAULTS:
        cur.execute(
            "INSERT INTO feed_filters (field, pattern, note) VALUES (%s, %s, %s)",
            (field, pattern, note),
        )
    return len(DEFAULTS)


def load(cur):
    cur.execute(
        "SELECT id, field, pattern, note, enabled FROM feed_filters ORDER BY field, id"
    )
    cols = ("id", "field", "pattern", "note", "enabled")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def where_clause(cur, alias="a"):
    """(sql, params) excluding everything the enabled filters match."""
    cur.execute("SELECT field, pattern FROM feed_filters WHERE enabled")
    rows = [(f, p) for f, p in cur.fetchall() if f in FIELDS]
    if not rows:
        return "", []
    parts, params = [], []
    for field, pattern in rows:
        parts.append(f"coalesce({alias}.{field}, '') !~* %s")
        params.append(pattern)
    return " AND ".join(parts), params
