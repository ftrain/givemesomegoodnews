#!/bin/bash
# Retag whatever the rotation brought in, then regenerate the site.
set -uo pipefail
export PATH=/srv/givemesomegoodnews/venv/bin:/usr/bin:/bin
export PY=/srv/givemesomegoodnews/venv/bin/python3
export DATABASE_URL=postgresql:///givemesomegoodnews
cd /srv/givemesomegoodnews/app
make classify
exec make build
