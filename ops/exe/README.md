# Box configuration for givemesomegood.exe.xyz

Installed by the workspace deploy tool (`scripts/deploy givemesomegood` on the
dev box) from the commit being deployed. Edit here and deploy.

| file | installed at |
|---|---|
| `nginx-gmsgn.conf` | `/etc/nginx/sites-available/gmsgn` — serves `site/`, proxies `/search` and `/admin` |
| `systemd/givemesomegoodnews-{search,admin}.service` | `/etc/systemd/system/` |
| `cron.d-givemesomegoodnews` | `/etc/cron.d/givemesomegoodnews` — rotate every 5 min, build every 15, trending at :07, nightly at 04:23 |
| `bin/*.sh` | `/srv/givemesomegoodnews/bin/` — the jobs cron runs |

The app lives in `/srv/givemesomegoodnews/app`, owned by the `givemesomegoodnews`
user. The crawl rewrites tracked files under `site/` and `data/catalog.json`
continuously, so a deploy ignores drift there and rebuilds the site afterwards;
it also waits for the `run/build.lock` and `run/crawl.lock` the cron jobs hold.

The jobs read `/srv/givemesomegoodnews/.env` (owned by `givemesomegoodnews`,
mode 600), the only place the keys are kept: `DEEPSEEK_API_KEY`, which
`trending.sh` uses to name trending topics, and `TYPESAFE_API_KEY`, which
`build.sh` and `nightly.sh` pass to `classify` for the Jev subject layer.
Without the first, trending topics are named by their headlines' shared
phrase; without the second, the stories the other subject layers leave blank
stay blank.

## A fresh VM

What it took to rebuild the box on 23 October 2026, after the VM was erased.
Everything here is done once; from then on `scripts/deploy givemesomegood`
owns the box. The commands run over ssh from the dev box.

1. **The VM.** `ssh exe.dev new --name=givemesomegood --disk=50GB --cpu=2
   --memory=4GB` (the comment, if any, must have no spaces). It comes up as
   `givemesomegood.exe.xyz`, with a new ssh host key: anything that reaches
   it in batch mode — the backup pull — must forget the old one first
   (`ssh-keygen -R givemesomegood.exe.xyz`).

2. **Packages.** `postgresql-16 postgresql-16-pgvector postgresql-client-16
   nginx zstd python3-venv python3-dev make git rsync`, then
   `systemctl enable --now postgresql nginx`. Remove nginx's stock site
   (`rm /etc/nginx/sites-enabled/default`), or the vhost deploy installs
   later fails with "duplicate default server".

3. **User, directories, database, venv.**

       useradd -r -m -d /srv/givemesomegoodnews -s /bin/bash givemesomegoodnews
       install -d -o givemesomegoodnews -g givemesomegoodnews \
           /srv/givemesomegoodnews/{app,run,log,bin}
       chmod 755 /srv/givemesomegoodnews
       sudo -u postgres createuser givemesomegoodnews
       sudo -u postgres createdb -O givemesomegoodnews givemesomegoodnews
       sudo -u postgres psql -d givemesomegoodnews -c 'create extension if not exists vector'
       sudo -u givemesomegoodnews python3 -m venv /srv/givemesomegoodnews/venv

4. **Secrets.** Write `/srv/givemesomegoodnews/.env` as the app user, mode
   600, with the two keys above. They are in use on the Aboard research box;
   pipe them across (`ssh a 'sudo grep -E "^(DEEPSEEK|TYPESAFE)_API_KEY=" …' |
   ssh b 'sudo -u givemesomegoodnews sh -c "umask 077; cat > …/.env"'`)
   rather than through anything that is logged.

5. **The checkout.** On the dev box, `scripts/deploy adopt givemesomegood`
   makes `app/` an empty sparse checkout of the commit on `origin/main`
   (`site/` and `data/catalog.json` are excluded, so the box's crawl can
   write them). It writes no files, so once as the app user on the box:
   `git -C /srv/givemesomegoodnews/app checkout -q -- .`

6. **Two things the first deploy cannot do for itself.** The deploy's build
   step runs `bin/build.sh` before it installs the `bin/` scripts, so copy
   them once by hand (`install -m 755 ops/exe/bin/*.sh /srv/givemesomegoodnews/bin/`);
   and `site/` is not git's, so make it, owned by the app user, or the build
   cannot write the pages (`install -d -o givemesomegoodnews -g givemesomegoodnews
   /srv/givemesomegoodnews/app/site/img`).

7. **The data.** `ops/backup/RESTORE.md`, from the newest night of the
   backup. Then `scripts/deploy givemesomegood`, which installs the vhosts,
   units, crontab and jobs, rebuilds the site and checks it.

8. **The proxy.** `ssh exe.dev share port givemesomegood 80`, `ssh exe.dev
   share set-public givemesomegood`, `ssh exe.dev domain add givemesomegood
   givemesomegood.news` and again for `www.givemesomegood.news`; one command
   per call. DNS at the registrar (apex ALIAS, `www` CNAME, both to
   `givemesomegood.exe.xyz`) needs no change.

9. **The gap.** `fetch_feeds --backfill <date of the night>` under the crawl
   lock, as RESTORE.md says; and start the backup pull again from home.
