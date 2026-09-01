#!/bin/sh
# /search, served live from Postgres. searchd.main() binds 127.0.0.1, which a
# container cannot expose, so the same handler is started on 0.0.0.0 here.
# The loop doubles as the wait for the database (load_menu queries it at boot).
set -u
cd /app

until python3 - <<'PY'
from http.server import ThreadingHTTPServer

from givemesomegoodnews import searchd

searchd.load_menu()
print("search listening on 0.0.0.0:8081", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8081), searchd.Handler).serve_forever()
PY
do
  echo "search service could not start (database not ready?); retrying in 3s"
  sleep 3
done
