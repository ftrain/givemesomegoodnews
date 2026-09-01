#!/bin/bash
set -e

export DEBIAN_FRONTEND=noninteractive

# The pipeline is Python with five small dependencies; psql applies schema.sql.
apt-get update
apt-get install -y --no-install-recommends python3 python3-pip python3-venv postgresql-client

# Install for the system python so any non-interactive shell (agent, dev server)
# can run `python3 -m givemesomegoodnews.*` without activating anything.
pip3 install --break-system-packages --no-cache-dir -r requirements.txt \
  || pip3 install --no-cache-dir -r requirements.txt

python3 -c "import psycopg, feedparser, yaml, requests, PIL; print('python deps ok')"
