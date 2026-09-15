#!/bin/bash
# Once an hour: what newsrooms are covering more than usual. Writes a snapshot
# to the database and rebuilds nothing; the next build renders trending.html.
set -uo pipefail
export PATH=/srv/givemesomegoodnews/venv/bin:/usr/bin:/bin
export PY=/srv/givemesomegoodnews/venv/bin/python3
export DATABASE_URL=postgresql:///givemesomegoodnews
# DEEPSEEK_API_KEY lives only here, never in the repository. Without it the
# topics are named by their headlines' shared phrase.
if [ -r /srv/givemesomegoodnews/.env ]; then
	set -a
	. /srv/givemesomegoodnews/.env
	set +a
fi
cd /srv/givemesomegoodnews/app
exec make trending
