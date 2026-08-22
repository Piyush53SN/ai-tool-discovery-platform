"""
Preference-vector recompute jobs — dispatched off the request path by
interactions/services.py after every bookmark/review/view write, and by the
periodic Celery beat task (settings.CELERY_BEAT_SCHEDULE) as a nightly safety
net. Same dispatch semantics as catalog embedding jobs (Section 6.5.2).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

from .services import recompute_preference_vector

logger = logging.getLogger(__name__)

_pool: ThreadPoolExecutor | None = None


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prefvec")
    return _pool


def _log_failure(fut) -> None:
    exc = fut.exception()
    if exc is not None:  # pragma: no cover
        logger.exception("Preference-vector recompute failed: %s", exc)


def dispatch_preference_recompute(user_id: int) -> None:
    mode = settings.EMBEDDING_DISPATCH  # shared knob: sync | celery | auto
    if mode == "sync":
        run_for_user(user_id)
        return
    if mode == "celery" or (mode == "auto" and settings.USE_CELERY):
        from .celery_tasks import recompute_preference_vector_task

        recompute_preference_vector_task.delay(user_id)
        return
    future = _get_pool().submit(run_for_user, user_id)
    future.add_done_callback(_log_failure)


def run_for_user(user_id: int) -> None:
    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return
    recompute_preference_vector(user)


def run_for_all_users() -> int:
    """Used by `manage.py recompute_preferences` and the Celery beat task."""
    from django.contrib.auth import get_user_model

    count = 0
    for user_id in get_user_model().objects.values_list("pk", flat=True):
        run_for_user(user_id)
        count += 1
    logger.info("Recomputed preference vectors for %d users", count)
    return count
