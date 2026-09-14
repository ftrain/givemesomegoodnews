# Box configuration for givemesomegood.exe.xyz

Installed by the workspace deploy tool (`scripts/deploy givemesomegood` on the
dev box) from the commit being deployed. Edit here and deploy.

| file | installed at |
|---|---|
| `nginx-gmsgn.conf` | `/etc/nginx/sites-available/gmsgn` — serves `site/`, proxies `/search` and `/admin` |
| `systemd/givemesomegoodnews-{search,admin}.service` | `/etc/systemd/system/` |
| `cron.d-givemesomegoodnews` | `/etc/cron.d/givemesomegoodnews` — rotate every 5 min, build every 15, nightly at 04:23 |
| `bin/*.sh` | `/srv/givemesomegoodnews/bin/` — the jobs cron runs |

The app lives in `/srv/givemesomegoodnews/app`, owned by the `givemesomegoodnews`
user. The crawl rewrites tracked files under `site/` and `data/catalog.json`
continuously, so a deploy ignores drift there and rebuilds the site afterwards;
it also waits for the `run/build.lock` and `run/crawl.lock` the cron jobs hold.
