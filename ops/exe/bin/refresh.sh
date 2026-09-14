#!/bin/bash
# Recurring job: pull new stories, tag them, regenerate the static site.
# Idempotent — articles dedupe on canonical URL, images on source-URL hash.
set -uo pipefail
export PATH=/srv/givemesomegoodnews/venv/bin:/usr/bin:/bin
export PY=/srv/givemesomegoodnews/venv/bin/python3
export DATABASE_URL=postgresql:///givemesomegoodnews
cd /srv/givemesomegoodnews/app
exec make refresh
