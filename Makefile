# givemesomegoodnews — catalog, map, and feed of new-model local news.
# `make all` goes from an empty database to a generated site.

DB_NAME ?= givemesomegoodnews
PY ?= python3

all: db seed about taglines institutions support feeds classify build

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

prune:
	$(PY) -m givemesomegoodnews.prune

classify:
	$(PY) -m givemesomegoodnews.classify

embed:
	$(PY) -m givemesomegoodnews.embed

build:
	$(PY) -m givemesomegoodnews.build_site

# What a cron job should run: pull new stories, regenerate the site.
refresh: feeds classify prune build

serve:
	$(PY) -m http.server 8000 --directory site

.PHONY: all db seed about taglines institutions support feeds classify prune embed build refresh serve
