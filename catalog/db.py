"""
pgvector ANN index management + query-time tuning.

Why this module exists (the interesting part):
----------------------------------------------
An ivfflat index is an *approximate* nearest-neighbour structure. It buckets
rows into `lists` k-means clusters at build time; a query only visits the
`probes` nearest lists. Two consequences:

  1. On a small table the planner happily uses the index with the default
     probes=1 while rows are scattered across lists -> silently missing
     results. (Classic ANN recall trap — bit us in tests: a 4-row table
     returned 1 row.)
  2. Above a few thousand rows the index is exactly what you want, as long
     as probes is tuned (rule of thumb: lists ~= rows/1000, probes ~ 10x
     more than you need for recall/speed balance).

So: the index is created ONLY when the catalog justifies it
(MIN_ROWS_FOR_IVFFLAT), with lists scaled to the row count, and every
vector-ordered query runs inside `ivfflat_probes(n)` which lifts probes for
the duration of one transaction — exact behaviour when the index is absent,
tuned recall when it exists.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

from django.db import connection, transaction

logger = logging.getLogger(__name__)

INDEX_NAME = "tool_embedding_ivfflat"
MIN_ROWS_FOR_IVFFLAT = 500
QUERY_PROBES = 50  # raised probes for our ordered-by-distance queries


def ivfflat_index_exists() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s", [INDEX_NAME]
        )
        return cursor.fetchone() is not None


def create_ivfflat_index(force: bool = False, lists: int | None = None) -> str | None:
    """Create the ANN index if the catalog is big enough (or force=True).

    Returns a human-readable status, or None when nothing was done.
    CONCURRENTLY (non-blocking for live traffic) is used unless we are inside
    a transaction — Postgres forbids it there (tests, migrations).
    """
    if ivfflat_index_exists():
        return "already exists"
    from catalog.models import Tool

    rows = Tool.objects.count()
    if rows < MIN_ROWS_FOR_IVFFLAT and not force:
        return None  # too small to justify ANN (seq scan is exact & fast)

    lists = lists or max(100, rows // 1000)
    concurrently = "" if connection.in_atomic_block else "CONCURRENTLY "
    with connection.cursor() as cursor:
        cursor.execute(
            f"CREATE INDEX {concurrently}IF NOT EXISTS {INDEX_NAME} "
            f"ON catalog_tool USING ivfflat (embedding vector_cosine_ops) "
            f"WITH (lists = {int(lists)});"
        )
    logger.info("Created %s (rows=%d, lists=%d)", INDEX_NAME, rows, lists)
    return f"created with lists={lists}"


def drop_ivfflat_index() -> bool:
    if not ivfflat_index_exists():
        return False
    concurrently = "" if connection.in_atomic_block else "CONCURRENTLY "
    with connection.cursor() as cursor:
        cursor.execute(f"DROP INDEX {concurrently}IF EXISTS {INDEX_NAME};")
    return True


@contextmanager
def ivfflat_probes(probes: int = QUERY_PROBES):
    """Raise ivfflat.probes for the duration of one transaction.

    SET LOCAL scopes the GUC to the transaction, so pooled connections are
    untouched. Harmless when the index doesn't exist (a GUC set on a table
    without ANN path just changes nothing) and on non-postgres backends.
    """
    if connection.vendor != "postgresql":
        yield
        return
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ivfflat.probes = %s", [probes])
        yield
