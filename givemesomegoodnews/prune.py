"""Keep the image cache in check. Never deletes a story.

The archive is the point: a story that has aged out of a publisher's own
feed may exist nowhere else that is easy to find, so nothing here removes
an article row, ever.

What it does remove is cached image files that no article references any
more — left behind when a publisher swaps artwork, or when an article was
deleted upstream. That is pure garbage collection.

`--images-older-than N` is available for the day the disk actually gets
tight: it deletes the cached *copy* of images attached to stories older
than N days and clears image_file, leaving the story, its link, its text
and image_url untouched. It is off unless you ask for it.

Run: python3 -m givemesomegoodnews.prune [--dry-run] [--images-older-than N]
"""

import os
import sys

from .db import connect
from .images import cache_dir


def main():
    dry = "--dry-run" in sys.argv
    age = None
    if "--images-older-than" in sys.argv:
        age = int(sys.argv[sys.argv.index("--images-older-than") + 1])

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM articles")
        print(f"articles: {cur.fetchone()[0]} (never pruned — the archive is kept)")

        if age is not None:
            cur.execute(
                "SELECT count(*) FROM articles WHERE image_file IS NOT NULL "
                "AND coalesce(published_at, fetched_at) < now() - make_interval(days => %s)",
                (age,),
            )
            n = cur.fetchone()[0]
            print(f"cached images on stories older than {age} days: {n}")
            if n and not dry:
                # The story stays; only our local copy of the picture goes.
                cur.execute(
                    "UPDATE articles SET image_file = NULL "
                    "WHERE image_file IS NOT NULL AND coalesce(published_at, fetched_at) "
                    "< now() - make_interval(days => %s)",
                    (age,),
                )
                print(f"  released {n} (image_url kept for provenance)")

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
    print(f"unreferenced image files: {removed} ({freed / 1e6:.1f} MB)"
          f"{' — dry run' if dry else ' removed'}")


if __name__ == "__main__":
    main()
