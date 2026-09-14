#!/bin/bash
# One slice of the feed rotation. Cheap, and safe to run often.
set -uo pipefail
export PATH=/srv/givemesomegoodnews/venv/bin:/usr/bin:/bin
export PY=/srv/givemesomegoodnews/venv/bin/python3
export DATABASE_URL=postgresql:///givemesomegoodnews
export CRAWL_WORKERS=${CRAWL_WORKERS:-16}
cd /srv/givemesomegoodnews/app
exec make rotate
