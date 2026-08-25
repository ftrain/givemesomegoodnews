import psycopg

from . import config


def connect():
    return psycopg.connect(config.DATABASE_URL, autocommit=True)


def vec_literal(vec):
    """Format a Python list of floats as a pgvector input literal."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def log_fetch(cur, org_slug, kind, url, ok, detail=""):
    cur.execute(
        "INSERT INTO fetch_log (org_slug, kind, url, ok, detail) VALUES (%s, %s, %s, %s, %s)",
        (org_slug, kind, url, ok, detail[:500]),
    )
