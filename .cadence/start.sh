#!/bin/bash
set -e

# Postgres 15 with pgvector is pre-baked on the runner and trusts localhost.
service postgresql start
until pg_isready -h localhost >/dev/null 2>&1; do sleep 1; done

createdb -h localhost -U postgres givemesomegoodnews 2>/dev/null || true

export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/givemesomegoodnews
# The app reads DATABASE_URL from the process environment only, so persist it
# everywhere a later shell or the dev server might pick it up.
grep -q "^DATABASE_URL=" .env 2>/dev/null || echo "DATABASE_URL=$DATABASE_URL" >> .env
grep -q "^DATABASE_URL=" /etc/environment 2>/dev/null || echo "DATABASE_URL=$DATABASE_URL" >> /etc/environment
grep -q "DATABASE_URL" "$HOME/.bashrc" 2>/dev/null || echo "export DATABASE_URL=$DATABASE_URL" >> "$HOME/.bashrc"

# schema.sql is the migration: idempotent CREATE ... IF NOT EXISTS + ALTERs.
until psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f schema.sql; do
  echo "waiting for postgres before applying schema.sql"
  sleep 2
done

# Seed the catalog (best effort; a bad row should not block the session).
python3 -m givemesomegoodnews.seed || true

mkdir -p site/img
echo "database ready: $DATABASE_URL"
