# localpaper — catalog, map, and feed of new-model local news.
# `make all` goes from an empty database to a generated site.

DB_NAME ?= localpaper
PY ?= python3

all: db seed about feeds build

db:
	createdb $(DB_NAME) 2>/dev/null || true
	psql -d $(DB_NAME) -f schema.sql

seed:
	$(PY) -m localpaper.seed

about:
	$(PY) -m localpaper.fetch_about

feeds:
	$(PY) -m localpaper.fetch_feeds

embed:
	$(PY) -m localpaper.embed

build:
	$(PY) -m localpaper.build_site

# What a cron job should run: pull new stories, regenerate the site.
refresh: feeds build

serve:
	$(PY) -m http.server 8000 --directory site

.PHONY: all db seed about feeds embed build refresh serve
