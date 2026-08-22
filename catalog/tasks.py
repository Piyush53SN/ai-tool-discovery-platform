"""
Embedding job dispatch — strictly OFF the request/response cycle.

`generate_tool_embedding` is the (synchronous, unit-testable) core. It is
invoked through `dispatch_tool_embedding`, which picks a runner based on
settings.EMBEDDING_DISPATCH:

    auto   -> Celery when USE_CELERY=1, otherwise an in-process background
              thread pool (dev / single-box deployments without Redis).
    celery -> always Celery (requires a broker + running worker).
    sync   -> inline execution; ONLY for the test suite.

Nothing in a DRF view ever calls the generator directly.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now

from .embeddings import get_embedder
from .models import Tool

logger = logging.getLogger(__name__)

# Daemon workers: small pool keeps memory bounded; jobs are idempotent so a
# lost job on shutdown is recovered by the nightly backfill command.
_pool: ThreadPoolExecutor | None = None


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed")
    return _pool


def _log_failure(fut) -> None:
    exc = fut.exception()
    if exc is not None:  # pragma: no cover
        logger.exception("Background embedding job failed: %s", exc)


# ---------------------------------------------------------------------------
# Core job (synchronous, idempotent)
# ---------------------------------------------------------------------------
def generate_tool_embedding(tool_id: int, force: bool = False) -> bool:
    """(Re)compute one tool's embedding. Returns True when it was written.

    Skips rows that are already fresh unless `force=True` — that dedupes the
    two signals a normal creation fires (post_save + tags post_add).

    Uses `update_fields` deliberately:
      * it does NOT touch `updated_at`, keeping `embedding_is_stale` false and
        preventing the post_save signal from re-dispatching forever;
      * it does not re-fire M2M tag changes.
    """
    try:
        tool = Tool.objects.get(pk=tool_id)
    except Tool.DoesNotExist:
        logger.warning("Embedding job for missing tool id=%s skipped", tool_id)
        return False

    if not force and not tool.embedding_is_stale:
        return False  # a queued duplicate lost the race; nothing to do

    text = tool.embedding_input()  # name + description + tags
    vector = get_embedder().encode_one(text)

    # Write outside a transaction: embedding generation can take seconds with
    # the real model; we never want to hold row locks while encoding.
    updated = Tool.objects.filter(pk=tool_id).update(
        embedding=vector.tolist(),
        embedding_updated_at=now(),
    )
    if updated:
        logger.info(
            "Embedded tool %s (backend=%s)", tool.slug, get_embedder().name
        )
    return bool(updated)


def batch_generate_embeddings(tool_ids: list[int], force: bool = True) -> tuple[int, int]:
    """Embed many tools efficiently (used by seed / backfill commands).

    Encodes in ONE batched call (huge speed-up for the transformer backend)
    and writes results with bulk UPDATEs. Returns (ok, skipped).
    """
    tools = [
        t for t in Tool.objects.filter(pk__in=tool_ids).prefetch_related("tags")
        if force or t.embedding_is_stale
    ]
    if not tools:
        return 0, 0

    texts = [t.embedding_input() for t in tools]
    vectors = get_embedder().encode(texts)
    stamp = now()

    ok = 0
    with transaction.atomic():
        for tool, vector in zip(tools, vectors, strict=True):
            ok += Tool.objects.filter(pk=tool.pk).update(
                embedding=vector.tolist(), embedding_updated_at=stamp
            )
    return ok, len(tools) - ok


# ---------------------------------------------------------------------------
# Dispatch layer
# ---------------------------------------------------------------------------
def dispatch_tool_embedding(tool_id: int) -> None:
    """Enqueue embedding work off the request path (see module docstring)."""
    mode = settings.EMBEDDING_DISPATCH
    if mode == "sync":
        generate_tool_embedding(tool_id)
        return
    if mode == "celery" or (mode == "auto" and settings.USE_CELERY):
        from .celery_tasks import generate_tool_embedding_task

        generate_tool_embedding_task.delay(tool_id)
        return
    # default / "auto" without Celery -> background thread pool
    future = _get_pool().submit(generate_tool_embedding, tool_id)
    future.add_done_callback(_log_failure)
