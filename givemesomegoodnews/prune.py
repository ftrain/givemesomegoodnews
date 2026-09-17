"""Keep the image cache in check. Never deletes a story.

The archive is the point: a story that has aged out of a publisher's own
feed may exist nowhere else that is easy to find, so nothing here removes
an article row, ever.

What it does remove is cached image files that no article references any
more — left behind when a publisher swaps artwork, or when an article was
deleted upstream. That is pure garbage collection.

`--stale-pages N` does the same for generated pages. Every page the site has
is written again by every build, a few times an hour, so a page untouched for
days is one the build no longer makes: a newsroom that has left the catalog,
a feed page from when the feed was longer, a name a page used to have. The
pictures, fonts and flags are left alone — they are not pages, and the
images have their own rule above.

`--images-older-than N` is available for the day the disk actually gets
tight: it deletes the cached *copy* of images attached to stories older
than N days and clears image_file, leaving the story, its link, its text
and image_url untouched. It is off unless you ask for it.

Run: python3 -m givemesomegoodnews.prune [--dry-run] [--images-older-than N]
                                        [--stale-pages N]
"""

import os
import sys
import time

from .db import connect
from . import config, images

# Directories under site/ that are not pages: the picture cache has its own
# garbage collection above, and fonts and flags are copied in from assets/.
NOT_PAGES = ("img", "fonts", "flags")


def stale_pages(site_dir, days, now=None):
    """Generated files no build has rewritten in `days` days."""
    cutoff = (now if now is not None else time.time()) - days * 86400
    found = []
    for root, dirs, names in os.walk(site_dir):
        if os.path.samefile(root, site_dir):
            dirs[:] = [d for d in dirs if d not in NOT_PAGES]
        for name in names:
            path = os.path.join(root, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    found.append(path)
            except OSError:
                continue
    return sorted(found)


def main():
    dry = "--dry-run" in sys.argv
    age = None
    if "--images-older-than" in sys.argv:
        age = int(sys.argv[sys.argv.index("--images-older-than") + 1])
    page_age = None
    if "--stale-pages" in sys.argv:
        page_age = int(sys.argv[sys.argv.index("--stale-pages") + 1])

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

    removed = freed = 0
    for name, path in images.every_file():
        if name in referenced:
            continue
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

    if page_age is not None:
        pages = stale_pages(config.SITE_DIR, page_age)
        size = 0
        for path in pages:
            try:
                size += os.path.getsize(path)
                if not dry:
                    os.unlink(path)
            except OSError:
                continue
        print(f"pages no build has written in {page_age} days: {len(pages)} "
              f"({size / 1e6:.1f} MB){' — dry run' if dry else ' removed'}")


if __name__ == "__main__":
    main()
