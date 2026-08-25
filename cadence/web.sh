#!/bin/sh
# The site: apply the schema, seed the catalog, keep crawling and rebuilding
# in the background, and serve site/ in the foreground.
set -u
cd /app

echo "applying schema.sql (waits for the database)"
until psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f schema.sql; do
  echo "database not ready yet; retrying in 3s"
  sleep 3
done

echo "seeding the catalog"
until python3 -m givemesomegoodnews.seed; do
  echo "seed failed; retrying in 5s"
  sleep 5
done

mkdir -p site/img

(
  # First pass: about text, taglines and support links. All three skip what
  # they already have, so a restart is cheap.
  python3 -m givemesomegoodnews.fetch_about || true
  python3 -m givemesomegoodnews.taglines || true
  python3 -m givemesomegoodnews.fetch_support || true

  # The recurring job, the same one the README puts in cron: pull a slice of
  # the feed rotation, retag, regenerate the site, garbage-collect images.
  while true; do
    python3 -m givemesomegoodnews.fetch_feeds --rotate "${ROTATE_BATCH:-90}" || true
    python3 -m givemesomegoodnews.classify || true
    python3 -m givemesomegoodnews.build_site || true
    python3 -m givemesomegoodnews.prune || true
    sleep "${REFRESH_SECONDS:-900}"
  done
) &

exec python3 -m http.server 8000 --directory site
