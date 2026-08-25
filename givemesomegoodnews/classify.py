"""Spread subject tags to articles no publisher labeled, using the vectors.

Layers 1 and 2 (publisher categories, URL paths — see
:mod:`givemesomegoodnews.subjects`) label most articles with no guesswork.
This handles the remainder.

The method is nearest-prototype: average the embeddings of every
confidently-labeled article in a subject into one centroid, then give each
unlabeled article the nearest centroid, provided it is both close enough in
absolute terms and clearly closer than the runner-up.

Why this works with the hashing embedder and matching the bare word
"Sports" does not: a centroid built from hundreds of sports stories is
dense in the vocabulary sports stories actually use — team names, "coach",
"season", "playoff" — while the token "sports" by itself is one hashed
bucket that most sports articles never contain.
"""

import json
import math
import os
import sys
from collections import Counter, defaultdict

from .db import connect
from .subjects import SUBJECTS

# An article must be at least this close to a centroid to take its label...
MIN_SIM = float(os.environ.get("MIN_SUBJECT_SIM", "0.10"))
# ...and this much closer to the winner than to the runner-up, so genuinely
# ambiguous stories stay untagged rather than getting a coin-flip subject.
MIN_MARGIN = float(os.environ.get("MIN_SUBJECT_MARGIN", "0.02"))
# Below this many labeled examples a centroid is too noisy to trust.
MIN_SEEDS = int(os.environ.get("MIN_SUBJECT_SEEDS", "12"))


def _parse(vec):
    return json.loads(vec) if isinstance(vec, str) else list(vec)


def _normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


def build_centroids(cur):
    """One L2-normalized prototype vector per subject with enough seeds."""
    cur.execute(
        """
        SELECT subject, embedding FROM articles
        WHERE subject IS NOT NULL AND subject_source IN ('declared', 'url')
          AND embedding IS NOT NULL
        """
    )
    sums, counts = defaultdict(list), Counter()
    for subject, vec in cur.fetchall():
        vec = _parse(vec)
        if not sums[subject]:
            sums[subject] = [0.0] * len(vec)
        acc = sums[subject]
        for i, x in enumerate(vec):
            acc[i] += x
        counts[subject] += 1

    centroids = {}
    for subject, acc in sums.items():
        if counts[subject] >= MIN_SEEDS:
            centroids[subject] = _normalize([x / counts[subject] for x in acc])
    return centroids, counts


def main():
    dry = "--dry-run" in sys.argv
    with connect() as conn, conn.cursor() as cur:
        centroids, counts = build_centroids(cur)
        if not centroids:
            print("classify: no subject has enough labeled seeds yet; nothing to do")
            return
        print("centroids from declared/url labels:")
        for subject in SUBJECTS:
            n = counts.get(subject, 0)
            mark = "" if subject in centroids else f"  (skipped, under {MIN_SEEDS})"
            if n:
                print(f"  {subject:<12} {n:5d} seeds{mark}")

        cur.execute(
            "SELECT id, embedding FROM articles "
            "WHERE subject IS NULL AND embedding IS NOT NULL"
        )
        rows = cur.fetchall()
        print(f"unlabeled articles: {len(rows)}")

        assigned, ambiguous, far = Counter(), 0, 0
        updates = []
        for article_id, vec in rows:
            vec = _normalize(_parse(vec))
            scored = sorted(
                ((sum(a * b for a, b in zip(vec, c)), s) for s, c in centroids.items()),
                reverse=True,
            )
            best_sim, best_subject = scored[0]
            runner = scored[1][0] if len(scored) > 1 else 0.0
            if best_sim < MIN_SIM:
                far += 1
                continue
            if best_sim - runner < MIN_MARGIN:
                ambiguous += 1
                continue
            updates.append((best_subject, article_id))
            assigned[best_subject] += 1

        if not dry:
            for subject, article_id in updates:
                cur.execute(
                    "UPDATE articles SET subject = %s, subject_source = 'vector' WHERE id = %s",
                    (subject, article_id),
                )

        print(f"assigned {len(updates)}{' (dry run)' if dry else ''}; "
              f"{ambiguous} too ambiguous, {far} below sim {MIN_SIM}")
        for subject, n in assigned.most_common():
            print(f"  {subject:<12} {n:5d}")

        cur.execute(
            "SELECT coalesce(subject, '(untagged)'), coalesce(subject_source, '-'), count(*) "
            "FROM articles GROUP BY 1, 2 ORDER BY 3 DESC"
        )
        print("\nfinal tally (subject, source, count):")
        for subject, source, n in cur.fetchall():
            print(f"  {subject:<12} {source:<9} {n:5d}")


if __name__ == "__main__":
    main()
