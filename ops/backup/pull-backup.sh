#!/usr/bin/env bash
# Pull a backup of givemesomegood.news onto this machine.
#
# One way, and it never erases. Everything is copied down, nothing is ever
# copied up, nothing on the server is changed, and nothing already here is
# deleted or overwritten: each night's database backup lands in its own dated
# directory, and pictures are only ever added. Run it from a home machine
# that can ssh to the VM.
#
#   crontab:  30 3 * * * /path/to/pull-backup.sh >> ~/backups/givemesomegood/pull.log 2>&1
#
# Settings, all optional:
#   DEST=~/backups/givemesomegood   where the copies go
#   HOST=givemesomegood.exe.xyz     the VM
#   DB=givemesomegoodnews           the database on it
#   APP_DIR=/srv/givemesomegoodnews/app
#   KEEP_ENV=1                      also copy the VM's .env (it holds secrets)
#
# What a night costs: about 20 MB of database, plus that day's new pictures
# (roughly 20 MB). The first run copies the whole picture cache, 1.4 GB.
#
# What it does not copy, on purpose:
#
#   - The embedding column. It is more than half the dump, and it is computed
#     from the headline and summary that are backed up: `make embed` rebuilds
#     it. (Only true while EMBEDDER=hashing, which is deterministic and local.
#     If a model embedder is ever used, back the column up instead.)
#   - Indexes. No pg_dump ever contains index data — only the statements that
#     rebuild them, which is what schema.sql in the repository already is.
#   - The generated site. Every page is rebuilt from the database in minutes.
#   - fetch_log, which is crawl bookkeeping, not the archive.
#
# Restoring is described in RESTORE.md, written beside the backups.
#
# Nothing here ever deletes. One consequence: the picture cache moved into
# subdirectories on 17 September 2026, so a copy pulled before that holds both
# layouts — the same bytes twice. Once img/<two>/<name> is there, the files
# left at the top of img/ are duplicates and can go:
#
#   find "$DEST/img" -maxdepth 1 -type f -exec sh -c \
#     'test -f "$(dirname "$1")/$(basename "$1" | cut -c1-2)/$(basename "$1")" && rm "$1"' _ {} \;
set -euo pipefail

DEST="${DEST:-$HOME/backups/givemesomegood}"
HOST="${HOST:-givemesomegood.exe.xyz}"
DB="${DB:-givemesomegoodnews}"
APP_DIR="${APP_DIR:-/srv/givemesomegoodnews/app}"
KEEP_ENV="${KEEP_ENV:-0}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=20)

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
# A night is built under .part and named only once everything is in it and
# checked, so a run that dies half way leaves something plainly unfinished
# rather than a directory that looks like a backup.
night="$DEST/db/$stamp.part"
say() { printf '%s  %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
mkdir -p "$night" "$DEST/img"

say "backing up $DB on $HOST to $DEST"

# 1. Everything except the two big tables' rows: schema, every index
#    definition, the catalog, the filters, the trending snapshots. Small.
say "database: schema and the small tables"
"${SSH[@]}" "$HOST" "sudo -u postgres pg_dump -Fc -Z 6 \
	--exclude-table-data=articles --exclude-table-data=fetch_log $DB" \
	>"$night/schema-and-tables.dump.part"
mv "$night/schema-and-tables.dump.part" "$night/schema-and-tables.dump"

# 2. The archive itself. The column list is asked for rather than written
#    down, so a new column is backed up the night it appears; the generated
#    search column and the embedding are the two that are left out. CSV with
#    a header, so the file says what its own columns are.
say "database: the articles, without the embedding"
columns="$("${SSH[@]}" "$HOST" "sudo -u postgres psql -d $DB -Atc \"
	select string_agg(quote_ident(column_name), ', ' order by ordinal_position)
	from information_schema.columns
	where table_name = 'articles' and is_generated = 'NEVER'
	  and column_name <> 'embedding'\"")"
[ -n "$columns" ] || { echo "could not read the column list" >&2; exit 1; }
printf '%s\n' "$columns" >"$night/articles.columns"
"${SSH[@]}" "$HOST" "sudo -u postgres psql -d $DB -Atc \"copy (select $columns \
	from articles) to stdout with (format csv, header true)\" | zstd -6 -c" \
	>"$night/articles.csv.zst.part"
mv "$night/articles.csv.zst.part" "$night/articles.csv.zst"

# 3. Check what arrived before calling it a backup. A dump that cannot be
#    listed and an archive that cannot be decompressed are not backups.
say "checking"
if command -v zstd >/dev/null; then
	zstd -t "$night/articles.csv.zst"
else
	say "  zstd not installed here; the archive was not verified"
fi
if command -v pg_restore >/dev/null; then
	pg_restore -l "$night/schema-and-tables.dump" >/dev/null
else
	say "  pg_restore not installed here; the dump was not verified"
fi

# 4. The pictures. Content-addressed and immutable, so --ignore-existing is
#    exactly right: a name that is already here is the same bytes, and the
#    day's new pictures are all that crosses. No --delete, ever.
say "pictures"
# macOS ships rsync 2.6.9 (and newer versions ship openrsync), neither of
# which has everything this needs. --stats and --ignore-existing are the two
# that matter; say so plainly rather than half-copying the cache.
# (Ask in one piece: grep -q would close the pipe and kill rsync mid-sentence,
# which under pipefail reads as "this rsync cannot do it".)
case "$(rsync --help 2>&1)" in
*--ignore-existing*) ;;
*)
	echo "this rsync has no --ignore-existing; install a current one (brew install rsync)" >&2
	exit 1
	;;
esac
rsync -a --ignore-existing --stats \
	"$HOST:$APP_DIR/site/img/" "$DEST/img/" |
	sed -n '/Number of files:/s/^/  /p; /Total transferred file size/s/^/  /p'

# 5. What this backup is of, for whoever has to restore it.
say "manifest"
{
	echo "taken:     $stamp"
	echo "host:      $HOST"
	echo "database:  $DB"
	echo "commit:    $("${SSH[@]}" "$HOST" "sudo -u \"\$(stat -c %U $APP_DIR)\" git -C $APP_DIR rev-parse HEAD" 2>/dev/null || echo unknown)"
	echo "articles:  $("${SSH[@]}" "$HOST" "sudo -u postgres psql -d $DB -Atc 'select count(*) from articles'" 2>/dev/null || echo '?') rows"
	echo "columns:   $columns"
	echo "sizes:"
	du -h "$night"/* | sed 's/^/  /'
	echo "pictures:  $(find "$DEST/img" -type f | wc -l) files, $(du -sh "$DEST/img" | cut -f1)"
} >"$night/MANIFEST"
sed -i.bak "s#$night#$DEST/db/$stamp#g" "$night/MANIFEST" 2>/dev/null || true
rm -f "$night/MANIFEST.bak"
cat "$night/MANIFEST"

if [ "$KEEP_ENV" = 1 ]; then
	say "the VM's .env (secrets — keep this backup private)"
	mkdir -p "$DEST/secrets"
	umask 077
	"${SSH[@]}" "$HOST" "sudo cat /srv/givemesomegoodnews/.env" >"$DEST/secrets/env-$stamp"
fi

# Written once, never overwritten: a restore should read what was true when
# the backup it is restoring was taken.
[ -f "$DEST/RESTORE.md" ] || cat >"$DEST/RESTORE.md" <<'DOC'
# Restoring givemesomegood.news

Each directory under `db/` is one night, and stands alone. `img/` is the
picture cache, shared by all of them: it only ever grows.

Everything below assumes the repository (github.com/ftrain/givemesomegoodnews)
and a Postgres 16 with pgvector.

1. Make the database and its tables. schema.sql creates every table and index.

       createdb givemesomegoodnews
       psql -d givemesomegoodnews -f schema.sql

2. Drop the vector index before loading. Building it row by row during a bulk
   load is what makes a restore take hours instead of minutes.

       psql -d givemesomegoodnews -c 'drop index if exists articles_embedding_idx'

3. Load the small tables — the catalog, the filters, the trending snapshots.

       pg_restore --data-only --disable-triggers -d givemesomegoodnews \
           db/<night>/schema-and-tables.dump

4. Load the archive. The CSV carries its own header, and articles.columns says
   the same thing.

       zstd -dc db/<night>/articles.csv.zst | psql -d givemesomegoodnews \
           -c "\copy articles($(cat db/<night>/articles.columns)) from stdin with (format csv, header true)"

5. Put the embeddings back and rebuild the vector index. The embedder is
   deterministic, so this recreates exactly what was there.

       DATABASE_URL=postgresql:///givemesomegoodnews python3 -m givemesomegoodnews.embed --all
       psql -d givemesomegoodnews -f schema.sql

6. Put the pictures where the site looks for them, and rebuild.

       rsync -a img/ <checkout>/site/img/
       make build

To check a backup without restoring over anything, do all of it against a
database called something else and run `make serve`.
DOC

mv "$night" "$DEST/db/$stamp"
say "done: $DEST/db/$stamp"
