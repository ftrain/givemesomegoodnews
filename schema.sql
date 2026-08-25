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
