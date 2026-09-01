#!/bin/sh
# The operator's feed-management panel. admin.main() binds 127.0.0.1; the same
# handler is started on 0.0.0.0 here. Sign in with a one-time link issued by
#   python3 -m givemesomegoodnews.admin --login
set -u
cd /app

until python3 - <<'PY'
from http.server import ThreadingHTTPServer

from givemesomegoodnews import admin

print("admin listening on 0.0.0.0:8082", flush=True)
ThreadingHTTPServer(("0.0.0.0", 8082), admin.Handler).serve_forever()
PY
do
  echo "admin service could not start; retrying in 3s"
  sleep 3
done
