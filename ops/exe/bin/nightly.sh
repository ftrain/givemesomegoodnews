#!/bin/bash
# Once a day: a full sweep of every feed, refresh support links and
# taglines for anything new, and garbage-collect what nothing points at any
# more — orphaned pictures, and pages no build has written in a week (a
# newsroom that has left the catalog, a feed page from when the feed was
# longer). Every live page is rewritten a few times an hour.
set -uo pipefail
export PATH=/srv/givemesomegoodnews/venv/bin:/usr/bin:/bin
export PY=/srv/givemesomegoodnews/venv/bin/python3
export DATABASE_URL=postgresql:///givemesomegoodnews
export CRAWL_WORKERS=20
cd /srv/givemesomegoodnews/app
make feeds
make support
make taglines
make classify
make prune PRUNE_ARGS="--stale-pages 7"
exec make build
