#!/bin/bash
# Once a day: a full sweep of every feed, refresh support links and
# taglines for anything new, and garbage-collect orphaned images.
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
make prune
exec make build
