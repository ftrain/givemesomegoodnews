# Restoring givemesomegood.news

Each directory under `db/` is one night, and stands alone. `img/` is the
picture cache, shared by all of them: it only ever grows. `MANIFEST` in a
night says which commit of the repository was deployed when it was taken.

This procedure was used for real on 23 October 2026, when the VM was erased
and rebuilt from the night of 17 September: 101,280 articles, back on the
air in an afternoon. The numbered steps are what worked; the notes after
them are what the written procedure had missed until then.

Everything below assumes the repository (github.com/ftrain/givemesomegoodnews)
and a Postgres 16 with pgvector. **On a fresh VM**, provision the box first —
packages, user, database, venv, checkout — following "A fresh VM" in
`ops/exe/README.md`, then come back here. On the box the commands run as the
`givemesomegoodnews` user from `/srv/givemesomegoodnews/app`, with
`DATABASE_URL=postgresql:///givemesomegoodnews` and the venv's `python3`.

1. Make the database and its tables. schema.sql creates every table and index.

       psql -d givemesomegoodnews -f schema.sql

2. Drop the vector index before loading. Building it row by row during a bulk
   load is what makes a restore take hours instead of minutes.

       psql -d givemesomegoodnews -c 'drop index if exists articles_embedding_idx'

3. Load the small tables — the catalog, the filters, the trending snapshots.
   `--disable-triggers` needs a superuser, so this one runs as `postgres`.

       sudo -u postgres pg_restore --data-only --disable-triggers \
           -d givemesomegoodnews db/<night>/schema-and-tables.dump

4. Load the archive. The CSV carries its own header, and articles.columns says
   the same thing. Then move the id sequence past what was loaded: `\copy`
   writes the ids but never touches the sequence, and without this the next
   crawl's first insert collides with a restored row.

       zstd -dc db/<night>/articles.csv.zst | psql -d givemesomegoodnews \
           -c "\copy articles($(cat db/<night>/articles.columns)) from stdin with (format csv, header true)"
       psql -d givemesomegoodnews -c \
           "select setval(pg_get_serial_sequence('articles', 'id'), (select max(id) from articles))"

5. Put the embeddings back and rebuild the vector index. The embedder is
   deterministic, so this recreates exactly what was there.

       python3 -m givemesomegoodnews.embed --all
       psql -d givemesomegoodnews -f schema.sql

6. Put the pictures where the site looks for them. `site/` is not written by
   git on the box (it is excluded from the checkout), so make it, owned by
   the app user, before copying into it; `--ignore-existing` because the
   files are content-addressed and a name already there is the same bytes.

       install -d -o givemesomegoodnews -g givemesomegoodnews site/img
       rsync -a --ignore-existing img/ site/img/

7. Deploy, which installs the box configuration, rebuilds the site and
   health-checks it: `scripts/deploy givemesomegood` on the dev box.

8. Fill the gap between the backup and today from the newsrooms' own feeds.
   Most feeds page back a few weeks (RFC 5005 `rel="next"`, or WordPress's
   `?paged=N`); this takes the crawl lock so the quarter-hourly build waits.

       flock /srv/givemesomegoodnews/run/crawl.lock \
           python3 -m givemesomegoodnews.fetch_feeds --backfill <date of the night>

   Expect about half: a five-week gap refilled at 10–13k stories a week
   against the 25k a week the crawl had been seeing live, and 72 of 769
   newsrooms gave nothing back. The next builds tag what arrived (the
   subject layers, Jev included, run inside `classify`).

To check a backup without restoring over anything, do all of it against a
database called something else and run `make serve`.

## After a rebuild

- **The home machine's nightly pull will fail** until the VM's old host key
  is forgotten: a rebuilt VM has a new one, and the pull runs ssh in batch
  mode, which refuses a changed key rather than asking. On the machine that
  pulls:

      ssh-keygen -R givemesomegood.exe.xyz

  (or whatever `HOST` the pull uses), then run the pull once by hand.
- **The secrets** are not in any backup unless `KEEP_ENV=1` was set. The
  site's `.env` holds `DEEPSEEK_API_KEY` and `TYPESAFE_API_KEY`; the same
  keys are in use on the Aboard research box, and can be piped from there
  over ssh without ever being printed.
- **The exe.dev side** has to be told again: `ssh exe.dev share port
  givemesomegood 80`, `share set-public givemesomegood`, `domain add
  givemesomegood givemesomegood.news` and the same for `www`. DNS at the
  registrar points the apex at `givemesomegood.exe.xyz` and needs nothing.
