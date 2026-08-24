"""
Hybrid search (Section 6.1): full-text x semantic, blended in SQL.

    hybrid_score = a * normalised(SearchRank) + (1 - a) * (1 - cosine_distance)

  * Full-text side: PostgreSQL tsvector/tsquery via SearchVector over
    name + description (name weighted A, description B). The vector is
    computed per row here — fine at catalog scale; the production optimisation
    is a stored generated tsvector column + GIN index (documented in the
    README, deliberately not bolted on for a few hundred rows).
  * Semantic side: the query string is embedded with the SAME model/dimension
    as the tools (catalog/embeddings.py, cached) and compared against
    Tool.embedding via pgvector's cosine-distance operator `<=>` *inside the
    database*.
  * Fusion: SearchRank is unbounded, so it is normalised by the window-MAX
    rank of the current filtered set (-> [0, 1]) before blending. Semantic
    similarity is 1 - distance; rows whose embedding has not landed yet get
    similarity 0 ("no evidence") rather than being dropped.

alpha = settings.SEARCH_TEXT_WEIGHT (default 0.4 — biased toward semantic
matches, with exact keyword hits still winning when they exist).
"""
from __future__ import annotations

import logging

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db.models import (
    Case,
    ExpressionWrapper,
    F,
    FloatField,
    Max,
    Value,
    When,
    Window,
)
from django.db.models.functions import Coalesce
from pgvector.django import CosineDistance

from .embeddings import embed_query

logger = logging.getLogger(__name__)


class HybridSearchFilter:
    """DRF filter backend: activates only when ?search= is present.

    Runs LAST in the backend chain so facets/filters are applied first and
    the hybrid ordering (order_by below) overrides any earlier ordering.
    """

    def filter_queryset(self, request, queryset, view):
        query = request.query_params.get("search", "").strip()
        if not query:
            return queryset
        view.search_query = query  # surfaced in the response for transparency
        return hybrid_search(queryset, query)


def hybrid_search(queryset, query: str, alpha: float | None = None):
    from django.conf import settings

    alpha = settings.SEARCH_TEXT_WEIGHT if alpha is None else alpha

    search_query = SearchQuery(query)
    search_vector = SearchVector("name", weight="A") + SearchVector("description", weight="B")

    query_vector = embed_query(query).tolist()  # 384-dim, unit length (cached)

    return (
        queryset.annotate(fts_raw=SearchRank(search_vector, search_query))
        # Window-MAX bounds the raw rank to (0, 1] relative to the best
        # keyword hit *within this filtered result set*.
        .annotate(max_fts=Window(expression=Max("fts_raw")))
        .annotate(
            fts_norm=Case(
                When(max_fts__gt=0, then=ExpressionWrapper(F("fts_raw") / F("max_fts"), output_field=FloatField())),
                default=Value(0.0),
                output_field=FloatField(),
            )
        )
        .annotate(
            # NULL embedding -> distance coalesced to 1.0 -> similarity 0.
            sem_sim=1.0 - Coalesce(CosineDistance("embedding", query_vector), Value(1.0)),
        )
        .annotate(
            hybrid_score=ExpressionWrapper(
                alpha * F("fts_norm") + (1.0 - alpha) * F("sem_sim"),
                output_field=FloatField(),
            )
        )
        .order_by("-hybrid_score", "-fts_raw")
    )
