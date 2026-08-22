"""
Personalised, embedding-based recommendation engine (Section 6.5 — the core).

Pipeline
========
1. SIGNAL   — every view/bookmark/review writes a weighted `Interaction` row
              (interactions/services.py).
2. PROFILE  — `recompute_preference_vector()` folds those rows into a single
              384-dim "taste" vector: for each tool the user interacted with,
              sum that user's weights (3 views + 1 bookmark on the same tool
              -> weight 6.0), then take the weight-weighted average of the
              tools' embeddings and L2-normalise. Cached on Profile.
3. RANK     — `recommend_for_user()` orders the catalog by cosine similarity
              between Tool.embedding and the preference vector using
              pgvector's `<=>` operator (CosineDistance) directly in SQL,
              excluding tools the user already bookmarked.
4. COLD START — no interactions yet: fall back to a popularity score
              (Bayesian-smoothed rating x bookmark traction), optionally
              boosted by the user's onboarding-tag picks.
5. DIVERSIFY — MMR re-rank penalises candidates whose category/tags nearly
              duplicate something already selected, so the top-N isn't a wall
              of near-identical tools.

Why cosine + pgvector instead of a separate vector DB: the vectors live in
the same Postgres tables as the relational data, so a single query can mix
`category`, `pricing_tier`, exclusions and ORDER BY embedding <=> :pref.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np
from django.conf import settings
from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Ln
from django.utils.timezone import now
from pgvector.django import CosineDistance

from catalog.models import Tool
from interactions.models import Interaction
from interactions.services import bookmarked_tool_ids, has_interaction_after

logger = logging.getLogger(__name__)

# Candidate multiplier: we fetch K*3 similar candidates from SQL before the
# MMR re-rank, so diversity never starves the final top-N.
CANDIDATE_MULTIPLIER = 3
# Bayesian prior weight for the smoothed average rating (see popularity score).
RATING_PRIOR_WEIGHT = 5.0
RATING_PRIOR_MEAN = 3.0  # neutral prior when the catalog has few ratings yet


# ---------------------------------------------------------------------------
# Step 2 — user preference vector
# ---------------------------------------------------------------------------
def recompute_preference_vector(user) -> list[float] | None:
    """Fold the user's interaction log into one cached 384-dim vector.

    Algorithm (deliberately transparent):
      a. Per-tool weight = SUM of that user's interaction weights on the tool
         (so one bookmark beats one view, and repeated views add up).
      b. preference = Σ(w_t * embedding_t) / Σ(w_t)   (weighted mean)
      c. L2-normalise so cosine distance against it is well-behaved.

    Tools without an embedding yet (e.g. created seconds ago) are skipped;
    if nothing usable remains the vector is cleared (None) -> cold start.
    """
    from accounts.models import Profile

    per_tool_weights = dict(
        Interaction.objects.filter(user=user)
        .values("tool_id")
        .annotate(total=Sum("weight"))
        .values_list("tool_id", "total")
    )
    if not per_tool_weights:
        Profile.objects.filter(user=user).update(
            preference_vector=None, preference_updated_at=now()
        )
        return None

    rows = Tool.objects.filter(
        pk__in=per_tool_weights, embedding__isnull=False
    ).values_list("id", "embedding")
    if not rows:
        Profile.objects.filter(user=user).update(
            preference_vector=None, preference_updated_at=now()
        )
        return None

    # pgvector hands us the embedding as a string "[0.1,0.2,...]" via psycopg2
    # or as pgvector.vector; normalise both to a numpy row.
    def to_row(value) -> np.ndarray:
        if isinstance(value, str):
            return np.fromstring(value.strip("[]"), sep=",", dtype=np.float32)
        return np.asarray(value, dtype=np.float32)

    matrix = np.vstack([to_row(vec) for _, vec in rows])
    weights = np.asarray(
        [per_tool_weights[tool_id] for tool_id, _ in rows], dtype=np.float32
    )

    preference = (weights @ matrix) / weights.sum()
    norm = np.linalg.norm(preference)
    if norm > 0:
        preference = preference / norm

    Profile.objects.filter(user=user).update(
        preference_vector=preference.tolist(),
        preference_updated_at=now(),
    )
    return preference.tolist()


def get_preference_vector(user, allow_lazy: bool = True) -> list[float] | None:
    """Read the cached vector; recompute lazily if missing or stale.

    Writes already trigger an off-path recompute (see interactions/services),
    so in the common case this is a single indexed read. The lazy path is a
    safety net (e.g. worker down), and is itself cheap: one aggregate over the
    user's interaction rows.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        from accounts.models import Profile

        profile = Profile.objects.create(user=user)

    if profile.preference_vector is not None and not has_interaction_after(
        user, profile.preference_updated_at
    ):
        return list(profile.preference_vector)

    if not allow_lazy:
        return list(profile.preference_vector) if profile.preference_vector else None

    return recompute_preference_vector(user)


def preference_is_fresh(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(
        profile
        and profile.preference_vector is not None
        and not has_interaction_after(user, profile.preference_updated_at)
    )


# ---------------------------------------------------------------------------
# Step 4 — cold start (no interactions yet)
# ---------------------------------------------------------------------------
def popularity_score(queryset=None):
    """SQL expression ranking tools by rating quality x engagement traction.

    quality = Bayesian smoothing `(v*R + m*C) / (v + m)` — shrinks a 5.0 from
    a single review toward the neutral prior until enough reviews arrive, so a
    lone 5/5 cannot outrank a 4.7 across 40 reviews.

    traction = 1 + ln(1 + bookmarks + 0.5*reviews) — a >=1x multiplier that
    grows with engagement but never zeroes out quiet-but-good tools (unlike a
    raw product of rating x bookmark_count, which collapses to 0 whenever a
    tool has no bookmarks yet).

    popularity = quality x traction.
    """
    annotated = (queryset or Tool.objects).annotate(
        smoothed_rating=ExpressionWrapper(
            (
                F("rating_count") * F("avg_rating")
                + Value(RATING_PRIOR_WEIGHT * RATING_PRIOR_MEAN)
            )
            / (F("rating_count") + Value(RATING_PRIOR_WEIGHT)),
            output_field=FloatField(),
        ),
    )
    return annotated.annotate(
        popularity=ExpressionWrapper(
            F("smoothed_rating")
            * (
                1.0
                + Ln(
                    ExpressionWrapper(
                        1.0
                        + Coalesce(F("bookmark_count"), Value(0.0))
                        + ExpressionWrapper(
                            0.5 * Coalesce(F("rating_count"), Value(0.0)),
                            output_field=FloatField(),
                        ),
                        output_field=FloatField(),
                    )
                )
            ),
            output_field=FloatField(),
        )
    )


def cold_start_candidates(user, limit: int):
    """Ranking for users with no interaction history.

    If the user picked onboarding tags (mini quiz), tools carrying those tags
    are surfaced first — match count acts as the primary sort key on top of
    the popularity score. With no tags, it is pure popularity.
    """
    profile = getattr(user, "profile", None)
    tag_ids: list[int] = (
        list(profile.onboarding_tags.values_list("id", flat=True)) if profile else []
    )

    queryset = popularity_score(Tool.objects.all())
    if tag_ids:
        queryset = queryset.annotate(
            tag_matches=Count("tags", filter=Q(tags__id__in=tag_ids), distinct=True)
        ).filter(tag_matches__gt=0)
        # tag_matches dominates; popularity breaks ties within a match count.
        queryset = queryset.order_by("-tag_matches", "-popularity", "-avg_rating")
        strategy = "cold_start_tags"
    else:
        queryset = queryset.order_by("-popularity", "-avg_rating")
        strategy = "cold_start_popularity"
    return queryset, strategy


# ---------------------------------------------------------------------------
# Step 3 + 5 — personalised ranking with MMR diversity re-rank
# ---------------------------------------------------------------------------
@dataclass
class Recommendation:
    tool: Tool
    similarity: float          # cosine similarity to the preference vector
    score: float               # post-diversity score (what the list is ordered by)
    reason: str                # human-readable explanation for the UI
    tags: set = field(default_factory=set, repr=False)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _content_signature(tool: Tool, tag_names: dict[int, set]) -> set:
    """Category slug + tag slugs — the 'sameness' axes for diversification."""
    return {f"cat:{tool.category.slug}"} | tag_names.get(tool.id, set())


def _mmr_diversify(
    candidates: list[Recommendation], limit: int, lam: float
) -> list[Recommendation]:
    """Maximal Marginal Relevance re-rank (stretch goal 6.5.5).

    Greedily build the answer list. At each step pick the candidate with the
    best marginal score:

        MMR = lam * sim(c, preference) - (1 - lam) * max_jaccard(c, selected)

    A candidate that is *very* similar to the taste vector but a near-duplicate
    of something already chosen (same category + overlapping tags) loses points
    to a slightly less similar but genuinely different tool. lam = 1 collapses
    back to pure similarity ranking.
    """
    pool = list(candidates)
    selected: list[Recommendation] = []

    while pool and len(selected) < limit:
        best_i, best_score = 0, -math.inf
        for i, cand in enumerate(pool):
            redundancy = max(
                (_jaccard(cand.tags, sel.tags) for sel in selected), default=0.0
            )
            mmr = lam * cand.similarity - (1.0 - lam) * redundancy
            if mmr > best_score:
                best_i, best_score = i, mmr
        chosen = pool.pop(best_i)
        chosen.score = round(best_score, 6)
        chosen.reason = chosen.reason if selected else chosen.reason
        selected.append(chosen)
    return selected


def recommend_for_user(user, limit: int | None = None, diversify: bool | None = None):
    """Return (ranked Recommendations, strategy) for `user`.

    Strategy is one of:
      personalised          — preference vector exists, cosine ranking + MMR
      cold_start_tags       — no interactions, onboarding tag boosting
      cold_start_popularity — no interactions, no tags, popularity fallback
    """
    limit = limit or settings.RECOMMENDATION_DEFAULT_LIMIT
    diversify = settings.RECOMMENDATION_DIVERSIFY if diversify is None else diversify
    lam = settings.RECOMMENDATION_MMR_LAMBDA

    preference = get_preference_vector(user)
    already_bookmarked = bookmarked_tool_ids(user)

    if preference is None:
        # ---------------- cold start ----------------
        queryset, strategy = cold_start_candidates(user, limit)
        recs = [
            Recommendation(
                tool=tool,
                similarity=0.0,
                score=float(tool.popularity),
                reason="Popular in the catalog"
                if strategy == "cold_start_popularity"
                else "Matches your onboarding interests",
            )
            for tool in queryset[:limit]
        ]
        return recs, strategy

    # ---------------- personalised cosine ranking (pgvector <=> in SQL) -----
    # Fetch limit*CANDIDATE_MULTIPLIER nearest neighbours in one indexed
    # query; exclusions (already bookmarked) compose with the vector sort.
    queryset = (
        Tool.objects.filter(embedding__isnull=False)
        .exclude(pk__in=already_bookmarked)
        .annotate(distance=CosineDistance("embedding", preference))
        .order_by("distance")[: limit * CANDIDATE_MULTIPLIER]
    )

    tag_names = {
        tool_id: set(names)
        for tool_id, names in _bulk_tag_slugs([t.pk for t in queryset])
    }

    candidates = [
        Recommendation(
            tool=tool,
            similarity=round(1.0 - float(tool.distance), 6),  # cos sim = 1 - dist
            score=round(1.0 - float(tool.distance), 6),
            reason="Similar to tools you interacted with",
            tags=_content_signature(tool, tag_names),
        )
        for tool in queryset
    ]
    if not candidates:
        return [], "personalised"

    if diversify and len(candidates) > 1:
        ranked = _mmr_diversify(candidates, limit, lam)
        strategy = "personalised_diverse"
    else:
        ranked = candidates[:limit]
        strategy = "personalised"
    return ranked, strategy


def _bulk_tag_slugs(tool_ids: list[int]):
    """(tool_id, {tag slugs}) pairs for a batch of tools — one extra query."""
    pairs = (
        Tool.tags.through.objects.filter(tool_id__in=tool_ids)
        .values_list("tool_id", "tag__slug")
    )
    grouped: dict[int, set] = {}
    for tool_id, slug in pairs:
        grouped.setdefault(tool_id, set()).add(slug)
    return grouped.items()


# ---------------------------------------------------------------------------
# Extras served by the same machinery
# ---------------------------------------------------------------------------
def similar_tools(tool: Tool, limit: int = 3):
    """Item-item similarity for the tool detail page ("you might also like").

    Classic content-based fallback: nearest neighbours by embedding cosine
    distance, same category pinned above others is NOT done here — pure
    semantic neighbours are more interesting for discovery.
    """
    if tool.embedding is None:
        return Tool.objects.none()
    return (
        Tool.objects.filter(embedding__isnull=False)
        .exclude(pk=tool.pk)
        .annotate(distance=CosineDistance("embedding", tool.embedding))
        .order_by("distance")[:limit]
    )


def trending_tools(days: int = 7, limit: int = 5):
    """'Trending' = bookmark velocity: bookmark interactions in the window."""
    from datetime import timedelta

    since = now() - timedelta(days=days)
    qs = (
        Tool.objects.filter(interactions__type="bookmark", interactions__created_at__gte=since)
        .annotate(recent_bookmarks=Count("interactions"))
        .order_by("-recent_bookmarks", "-avg_rating")[:limit]
    )
    return qs
