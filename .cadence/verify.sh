#!/bin/bash
set -eo pipefail

export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/givemesomegoodnews}"

echo "== byte-compile the pipeline =="
python3 -m compileall -q givemesomegoodnews

echo "== import every entry point (deps present?) =="
python3 -c "import givemesomegoodnews.build_site, givemesomegoodnews.searchd, givemesomegoodnews.admin, givemesomegoodnews.fetch_feeds, givemesomegoodnews.classify; print('imports ok')"

echo "== database reachable, schema applied, catalog seeded =="
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "SELECT count(*) AS orgs FROM orgs"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "SELECT count(*) AS articles FROM articles"
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "SELECT extname FROM pg_extension WHERE extname = 'vector'" | grep -q vector

echo "== embedder is deterministic and 384-dimensional =="
python3 -c "
from givemesomegoodnews.embedder import get_embedder
e = get_embedder()
v = e.embed(['school board recall in the county seat'])[0]
assert len(v) == 384, len(v)
assert e.embed(['school board recall in the county seat'])[0] == v
print('embedder ok', e.name)
"

echo "== search service boots and answers =="
python3 -m givemesomegoodnews.searchd 8181 >/tmp/verify-searchd.log 2>&1 &
search_pid=$!
trap 'kill "$search_pid" "${site_pid:-}" 2>/dev/null || true' EXIT
ok=""
for _ in $(seq 1 45); do
  curl -fsS "http://127.0.0.1:8181/search?q=housing" >/dev/null 2>&1 && ok=1 && break
  sleep 1
done
[ -n "$ok" ] || { echo "search service did not boot"; tail -n 80 /tmp/verify-searchd.log; exit 1; }
echo "search ok"

echo "== static site serves =="
python3 -m http.server 8188 --directory site >/tmp/verify-site.log 2>&1 &
site_pid=$!
ok=""
for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:8188/index.html" >/dev/null 2>&1 && ok=1 && break
  sleep 1
done
[ -n "$ok" ] || { echo "static site did not serve"; tail -n 40 /tmp/verify-site.log; exit 1; }
echo "static site ok"

echo "verify: ready"
