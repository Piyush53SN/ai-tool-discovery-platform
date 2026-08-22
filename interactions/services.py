"""
Business rules for bookmarks and reviews.

Every write goes through these service functions (never raw model saves) so the
invariants hold everywhere — API, admin, shell, tests:

  1. Tool.avg_rating / rating_count / bookmark_count stay consistent
     (denormalised for fast list rendering & cold-start ranking).
  2. Every behavioural event lands in the Interaction log with the right
     weight — this table is the recommender's only input.
  3. A user's cached preference vector is refreshed OFF the request path
     after every meaningful write (thread pool / Celery, like embeddings).

Interaction-log bookkeeping policy (deliberate, documented):
  * `view` rows are append-only (weak, repeated signal).
  * `bookmark` rows mirror the current bookmark: created with it, removed
    with it. An un-bookmark expresses a changed mind, and a taste vector that
    still craves the removed tool would be lying.
  * `review` rows are one-per-(user, tool) and re-weighted when the review is
    edited (weight depends on the rating).
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models import Avg, Count, F

from catalog.models import Tool

from .models import (
    INTERACTION_WEIGHTS,
    Bookmark,
    Interaction,
    InteractionType,
    Review,
    review_weight,
)

logger = logging.getLogger(__name__)



def _dispatch_pref(user_id: int) -> None:
    """Queue a preference-vector refresh off the request path.

    Imported lazily: recommendations.tasks imports this module for its query
    helpers, so a top-level import would be circular.
    """
    from recommendations.tasks import dispatch_preference_recompute

    dispatch_preference_recompute(user_id)


# ---------------------------------------------------------------------------
# Rating statistics (denormalised on Tool)
# ---------------------------------------------------------------------------
def recompute_rating_stats(tool: Tool) -> None:
    """Aggregate the tool's reviews into avg_rating / rating_count.

    Recomputing from scratch on every review write is exact (edits & deletes
    included) and cheap: reviews-per-tool is small, and it runs inside the
    same transaction as the triggering write.
    """
    stats = tool.reviews.aggregate(avg=Avg("rating"), n=Count("id"))
    tool.avg_rating = round(stats["avg"] or 0, 2)
    tool.rating_count = stats["n"] or 0
    # NB: `updated_at` is deliberately NOT bumped — rating stats are not part
    # of the embedding input, and touching it would mark every embedding stale
    # after each review (post_save -> needless re-embed jobs).
    tool.save(update_fields=["avg_rating", "rating_count"])


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------
@transaction.atomic
def set_bookmark(user, tool: Tool) -> tuple[Bookmark, bool]:
    """Bookmark a tool (idempotent) and log the weighted interaction."""
    bookmark, created = Bookmark.objects.get_or_create(user=user, tool=tool)
    if created:
        Tool.objects.filter(pk=tool.pk).update(bookmark_count=F("bookmark_count") + 1)
        Interaction.objects.update_or_create(
            user=user,
            tool=tool,
            type=InteractionType.BOOKMARK,
            defaults={"weight": INTERACTION_WEIGHTS[InteractionType.BOOKMARK]},
        )
        _dispatch_pref(user.id)
    return bookmark, created


@transaction.atomic
def remove_bookmark(bookmark: Bookmark) -> None:
    """Remove a bookmark together with its interaction row (see docstring)."""
    tool = bookmark.tool
    user = bookmark.user
    bookmark.delete()
    Tool.objects.filter(pk=tool.pk, bookmark_count__gt=0).update(
        bookmark_count=F("bookmark_count") - 1
    )
    Interaction.objects.filter(
        user=user, tool=tool, type=InteractionType.BOOKMARK
    ).delete()
    _dispatch_pref(user.id)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------
@transaction.atomic
def submit_review(user, tool: Tool, rating: int, comment: str = "") -> tuple[Review, bool]:
    """Create or update a (user, tool) review; maintain stats + interactions."""
    review, created = Review.objects.update_or_create(
        user=user,
        tool=tool,
        defaults={"rating": rating, "comment": comment},
    )
    recompute_rating_stats(tool)
    # One review interaction per (user, tool), re-weighted on edit:
    # rating >= 4 earns the enthusiasm bonus (4.0 -> 5.0).
    Interaction.objects.update_or_create(
        user=user,
        tool=tool,
        type=InteractionType.REVIEW,
        defaults={"weight": review_weight(rating)},
    )
    _dispatch_pref(user.id)
    return review, created


@transaction.atomic
def delete_review(review: Review) -> None:
    tool = review.tool
    user = review.user
    review.delete()
    recompute_rating_stats(tool)
    Interaction.objects.filter(user=user, tool=tool, type=InteractionType.REVIEW).delete()
    _dispatch_pref(user.id)


# ---------------------------------------------------------------------------
# View tracking
# ---------------------------------------------------------------------------
def record_view(user, tool: Tool) -> bool:
    """Log a `view` interaction for authenticated users (append-only).

    Anonymous views carry no identity and are skipped — a preference vector is
    per-user by definition. Returns True when a row was written.
    """
    if not user or not user.is_authenticated:
        return False
    Interaction.objects.create(
        user=user,
        tool=tool,
        type=InteractionType.VIEW,
        weight=INTERACTION_WEIGHTS[InteractionType.VIEW],
    )
    _dispatch_pref(user.id)
    return True


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def bookmarked_tool_ids(user) -> set[int]:
    if not user or not user.is_authenticated:
        return set()
    return set(
        Bookmark.objects.filter(user=user).values_list("tool_id", flat=True)
    )


def reviewed_tool_ids(user) -> set[int]:
    if not user or not user.is_authenticated:
        return set()
    return set(Review.objects.filter(user=user).values_list("tool_id", flat=True))


def has_interaction_after(user, moment) -> bool:
    """True if any interaction is newer than `moment` (staleness check)."""
    if moment is None:
        return True
    return Interaction.objects.filter(user=user, created_at__gt=moment).exists()


def user_interaction_summary(user) -> dict:
    """Small stats block used by the /api/auth/me/ endpoint."""
    rows = (
        Interaction.objects.filter(user=user)
        .values("type")
        .annotate(n=Count("id"))
    )
    return {row["type"]: row["n"] for row in rows}
