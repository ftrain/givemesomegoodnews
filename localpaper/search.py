"""Vector search over the article corpus.

    python3 -m localpaper.search "housing vouchers eviction" [-k 15]
"""

import sys

from .db import connect, vec_literal
from .embedder import get_embedder


def search(query, k=10):
    embedder = get_embedder()
    qvec = vec_literal(embedder.embed([query])[0])
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT o.name, o.city, o.state, a.title, a.url,
                   1 - (a.embedding <=> %s::vector) AS similarity
            FROM articles a JOIN orgs o ON o.id = a.org_id
            WHERE a.embedding IS NOT NULL
            ORDER BY a.embedding <=> %s::vector
            LIMIT %s
            """,
            (qvec, qvec, k),
        )
        return cur.fetchall()


def main():
    args = sys.argv[1:]
    k = 10
    if "-k" in args:
        i = args.index("-k")
        k = int(args[i + 1])
        del args[i : i + 2]
    if not args:
        sys.exit('usage: python3 -m localpaper.search "query" [-k 10]')
    for name, city, state, title, url, sim in search(" ".join(args), k):
        place = f"{city}, {state}" if city and state else (state or "national")
        print(f"{sim:.3f}  [{name} — {place}] {title}\n       {url}")


if __name__ == "__main__":
    main()
