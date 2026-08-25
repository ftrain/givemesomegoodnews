"""Keep the archive — and the disk — from growing without limit.

Roughly a thousand feeds produce several thousand new stories a day. Left
alone that fills a 50GB disk in about a year and slows every query on the
way. This drops stories past the retention window and then deletes cached
images nothing references any more.

Feeds only carry recent items anyway, so a pruned story is one no reader
could have reached from this site in months.

Run: python3 -m givemesomegoodnews.prune [--dry-run] [--days N]
"""

import os
import sys

from . import config
from .db import connect
from .images import cache_dir

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "120"))


def main():
    dry = "--dry-run" in sys.argv
    days = RETENTION_DAYS
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM articles "
            "WHERE coalesce(published_at, fetched_at) < now() - make_interval(days => %s)",
            (days,),
        )
        stale = cur.fetchone()[0]
        print(f"articles older than {days} days: {stale}")
        if stale and not dry:
            cur.execute(
                "DELETE FROM articles "
                "WHERE coalesce(published_at, fetched_at) < now() - make_interval(days => %s)",
                (days,),
            )
            print(f"  deleted {stale}")

        # Now the images nothing points at any more.
        cur.execute("SELECT image_file FROM articles WHERE image_file IS NOT NULL")
        referenced = {r[0] for r in cur.fetchall()}

    directory = cache_dir()
    removed = freed = 0
    for name in os.listdir(directory):
        if name in referenced:
            continue
        path = directory / name
        try:
            size = path.stat().st_size
            if not dry:
                path.unlink()
            removed += 1
            freed += size
        except OSError:
            continue
    print(f"orphaned images: {removed} ({freed / 1e6:.1f} MB){' — dry run' if dry else ' removed'}")

    if not dry and stale:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("VACUUM (ANALYZE) articles")
            print("  vacuumed articles")


if __name__ == "__main__":
    main()
