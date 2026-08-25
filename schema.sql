-- givemesomegoodnews schema. Requires the pgvector extension.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS orgs (
    id              SERIAL PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    about_url       TEXT,
    feed_url        TEXT,
    city            TEXT,
    state           TEXT,            -- two-letter code; NULL for no fixed geography
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    coverage        TEXT,            -- human description of the coverage area
    coverage_type   TEXT,            -- city | metro | state | regional | national | network
    model           TEXT,            -- ownership/funding model, short label
    beat            TEXT,            -- short topic label for topic-driven outlets
    tagline         TEXT,            -- one sentence from their About page
    features        TEXT[] DEFAULT '{}',  -- Black-owned, Spanish, INN member, Worker-owned...
    source          TEXT,            -- curated | mdp (which data file it came from)
    geo_precision   TEXT,            -- place | county | state, for imported coordinates
    timezone        TEXT,            -- IANA zone override; else derived from state
    affiliations    TEXT[] DEFAULT '{}',  -- e.g. {American Journalism Project, Lenfest Beyond Print}
    founded         INT,
    about_text      TEXT,            -- copied from their about page, in their words
    about_source_url TEXT,
    about_fetched_at TIMESTAMPTZ,
    -- Where a reader can pay this newsroom. Every feed item links here.
    support_url     TEXT,
    support_label   TEXT,            -- Donate | Subscribe | Become a member
    support_source  TEXT,            -- yaml | discovered | homepage
    support_checked_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS articles (
    id              SERIAL PRIMARY KEY,
    org_id          INT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    url             TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    summary         TEXT,
    author          TEXT,
    published_at    TIMESTAMPTZ,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    image_url       TEXT,            -- where the feed image came from
    image_file      TEXT,            -- our downscaled copy in site/img/; we never hotlink
    image_w         INT,             -- so pages can reserve the space before it loads
    image_h         INT,
    categories      TEXT[] DEFAULT '{}',  -- the publisher's own RSS categories, verbatim
    subject         TEXT,            -- our taxonomy; see givemesomegoodnews/subjects.py
    subject_source  TEXT,            -- declared | url | vector
    -- 384 dims matches the default local hashing embedder; see givemesomegoodnews/embedder.py
    -- for how to swap in a model-based embedder (requires re-embedding).
    embedding       vector(384)
);

CREATE INDEX IF NOT EXISTS articles_org_idx ON articles (org_id);
CREATE INDEX IF NOT EXISTS articles_pub_idx ON articles (published_at DESC);
CREATE INDEX IF NOT EXISTS articles_subject_idx ON articles (subject);
CREATE INDEX IF NOT EXISTS articles_embedding_idx ON articles
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS fetch_log (
    id          SERIAL PRIMARY KEY,
    org_slug    TEXT,
    kind        TEXT,        -- about | feed
    url         TEXT,
    ok          BOOLEAN,
    detail      TEXT,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Migrations. CREATE TABLE IF NOT EXISTS above builds a fresh database, but
-- it will not add a column to a table that already exists, so every column
-- added after the first release is repeated here. All are idempotent, and
-- deploy runs this file on every release.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_url      TEXT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_file     TEXT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_w        INT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_h        INT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS categories     TEXT[] DEFAULT '{}';
ALTER TABLE articles ADD COLUMN IF NOT EXISTS subject        TEXT;
ALTER TABLE articles ADD COLUMN IF NOT EXISTS subject_source TEXT;

ALTER TABLE orgs ADD COLUMN IF NOT EXISTS beat               TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS tagline            TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS features           TEXT[] DEFAULT '{}';
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS source             TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS geo_precision      TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS in_default         BOOLEAN DEFAULT true;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS language           TEXT;
CREATE INDEX IF NOT EXISTS orgs_default_idx ON orgs (in_default);
CREATE INDEX IF NOT EXISTS orgs_features_idx ON orgs USING gin (features);
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS timezone           TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS support_url        TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS support_label      TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS support_source     TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS support_checked_at TIMESTAMPTZ;

-- Full-text search. A generated column keeps the index in step with the row
-- automatically; two-argument to_tsvector with a literal config is immutable,
-- which is what lets it be GENERATED ... STORED.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(summary, '')), 'B')
    ) STORED;
CREATE INDEX IF NOT EXISTS articles_search_idx ON articles USING gin (search_tsv);

-- The funders, networks and associations behind the newsrooms.
CREATE TABLE IF NOT EXISTS institutions (
    id              SERIAL PRIMARY KEY,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    about_url       TEXT,
    kind            TEXT,            -- funder | network | association | program | research
    affiliation     TEXT,            -- matches orgs.affiliations, to cross-reference
    about_text      TEXT,
    about_source_url TEXT,
    tagline         TEXT,
    about_fetched_at TIMESTAMPTZ
);

-- Alt text as the publisher wrote it, so screen readers get their words.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS image_alt TEXT;

-- Indices for the queries the build and the search service actually run.
CREATE INDEX IF NOT EXISTS articles_org_pub_idx  ON articles (org_id, published_at DESC);
CREATE INDEX IF NOT EXISTS articles_subject_pub_idx ON articles (subject, published_at DESC)
    WHERE subject IS NOT NULL;
CREATE INDEX IF NOT EXISTS articles_recent_idx   ON articles (coalesce(published_at, fetched_at) DESC);
CREATE INDEX IF NOT EXISTS articles_image_idx    ON articles (image_file) WHERE image_file IS NOT NULL;
CREATE INDEX IF NOT EXISTS orgs_state_idx        ON orgs (state);
CREATE INDEX IF NOT EXISTS orgs_source_idx       ON orgs (source);
CREATE INDEX IF NOT EXISTS orgs_support_idx      ON orgs (support_url) WHERE support_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS fetch_log_slug_idx    ON fetch_log (org_slug, fetched_at DESC);

-- Admin: one operator, passwordless, tokens issued over SSH rather than email
-- (DigitalOcean blocks outbound port 25, and a relay is one more thing to leak).
CREATE TABLE IF NOT EXISTS admin_tokens (
    token_hash  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS admin_sessions (
    session_hash TEXT PRIMARY KEY,
    email        TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS admin_sessions_exp_idx ON admin_sessions (expires_at);

-- Edits made in the admin tool, kept apart from the yaml so that re-seeding
-- or re-importing the directory never silently undoes them.
CREATE TABLE IF NOT EXISTS org_overrides (
    slug        TEXT PRIMARY KEY,
    fields      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by  TEXT
);

ALTER TABLE orgs ADD COLUMN IF NOT EXISTS crawl_feed BOOLEAN NOT NULL DEFAULT true;

-- What the front page leaves out. Editable in the admin tool rather than
-- compiled in, because "that kind of thing" is a moving target.
CREATE TABLE IF NOT EXISTS feed_filters (
    id          SERIAL PRIMARY KEY,
    field       TEXT NOT NULL,          -- title | summary | url | subject
    pattern     TEXT NOT NULL,          -- case-insensitive POSIX regex
    note        TEXT,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feed_filters_enabled_idx ON feed_filters (enabled);

-- Rotation: the crawler takes the least-recently-checked feeds each run.
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS last_crawled_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS orgs_rotation_idx ON orgs (last_crawled_at NULLS FIRST)
    WHERE crawl_feed;
