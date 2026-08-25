"""(Re)compute article embeddings. Use --all after swapping embedders."""

import sys

from .db import connect, vec_literal
from .embedder import get_embedder

BATCH = 200


def main():
    everything = "--all" in sys.argv
    embedder = get_embedder()
    where = "" if everything else "WHERE embedding IS NULL"

    done = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT id, title, coalesce(summary, '') FROM articles {where} ORDER BY id")
        rows = cur.fetchall()
        for start in range(0, len(rows), BATCH):
            batch = rows[start : start + BATCH]
            vecs = embedder.embed([f"{title} {summary}" for _, title, summary in batch])
            for (article_id, _, _), vec in zip(batch, vecs):
                cur.execute(
                    "UPDATE articles SET embedding = %s WHERE id = %s",
                    (vec_literal(vec), article_id),
                )
            done += len(batch)
            print(f"  embedded {done}/{len(rows)}")
    print(f"embedded {done} articles with {embedder.name}")


if __name__ == "__main__":
    main()
