"""Move cached pictures into the subdirectories they belong in.

The cache used to be one directory holding every file. Files are now spread
over 256 subdirectories by the first two characters of their name (see
:mod:`givemesomegoodnews.images`), and this moves whatever is still at the
top level into them. Safe to run any time and as often as you like: it moves
files within one filesystem, so each is a rename, and a file already in its
place is left alone.

Run: python3 -m givemesomegoodnews.migrate_images [--dry-run]
"""

import sys

from . import images


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    directory = images.cache_dir()
    moved = failed = 0
    for name in images.flat_names(directory):
        source = directory / name
        target = images.path_for(name, write=True)
        if source == target:
            continue
        # A file only ever moves deeper into the directory it was listed from.
        # Anything else means source and target disagree about where the cache
        # is, and moving 60,000 pictures somewhere else is not a migration.
        if target.parent.parent != directory:
            print(f"images: {target} is not inside {directory}; nothing moved",
                  file=sys.stderr)
            return 2
        try:
            if not dry:
                source.replace(target)
            moved += 1
        except OSError as e:
            print(f"  {name}: {e}", file=sys.stderr)
            failed += 1
    where = "would move" if dry else "moved"
    print(f"images: {where} {moved} file(s) into subdirectories"
          f"{f', {failed} failed' if failed else ''}"
          f"{' (dry run)' if dry else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
