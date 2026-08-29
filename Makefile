# givemesomegoodnews — catalog, map, and feed of new-model local news.
# `make all` goes from an empty database to a generated site.

DB_NAME ?= givemesomegoodnews
# Feeds per rotation slice; at one slice every 5 minutes this covers
# roughly a thousand feeds in about an hour.
ROTATE_BATCH ?= 90
PY ?= python3

all: db seed about taglines institutions support feeds classify reporters build

db:
	createdb $(DB_NAME) 2>/dev/null || true
	psql -d $(DB_NAME) -f schema.sql

seed:
	$(PY) -m givemesomegoodnews.seed

about:
	$(PY) -m givemesomegoodnews.fetch_about

institutions:
	$(PY) -m givemesomegoodnews.fetch_institutions

taglines:
	$(PY) -m givemesomegoodnews.taglines

support:
	$(PY) -m givemesomegoodnews.fetch_support

feeds:
	$(PY) -m givemesomegoodnews.fetch_feeds

# One slice of the rotation — the feeds checked longest ago.
rotate:
	$(PY) -m givemesomegoodnews.fetch_feeds --rotate $(ROTATE_BATCH)

# Garbage-collects unreferenced cached images. Never deletes a story.
prune:
	$(PY) -m givemesomegoodnews.prune

classify:
	$(PY) -m givemesomegoodnews.classify

embed:
	$(PY) -m givemesomegoodnews.embed

# Reporter identities from the bylines of newly crawled stories. Runs before
# build, which renders a page per identity. --all re-reads the whole archive.
reporters:
	$(PY) -m givemesomegoodnews.reporters

build:
	$(PY) -m givemesomegoodnews.build_site

# What a cron job should run: pull new stories, regenerate the site.
refresh: feeds classify prune reporters build

# What the frequent job runs: pull a slice, retag, regenerate.
refresh-slice: rotate classify reporters build

serve:
	$(PY) -m http.server 8000 --directory site

.PHONY: all db seed about taglines institutions support feeds rotate classify prune embed reporters build refresh refresh-slice serve
