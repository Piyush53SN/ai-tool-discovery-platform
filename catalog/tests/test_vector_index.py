"""
Regression tests for the ivfflat ANN index management (catalog/db.py).

Background: an ivfflat index with default probes=1 on a tiny table silently
DROPS rows from vector-ordered queries (k-means lists are mostly empty; the
single probe visits one list). These tests pin both the trap and our
mitigations: index created only past a size threshold + probes raised in a
scoped transaction.
"""
import pytest
from django.db import connection
from pgvector.django import CosineDistance

from catalog.db import (
    INDEX_NAME,
    create_ivfflat_index,
    drop_ivfflat_index,
    ivfflat_probes,
)
from catalog.models import Tool
from conftest import unit_vector
from interactions.models import Bookmark, Interaction
from recommendations.services import recommend_for_user

pytestmark = pytest.mark.django_db


def _index_on_tools_table() -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s AND tablename = 'catalog_tool'",
            [INDEX_NAME],
        )
        return cursor.fetchone() is not None


@pytest.mark.django_db(transaction=True)  # index DDL needs real commits
class TestIvfflatLifecycle:
    def test_not_created_below_threshold(self, category, make_tool):
        make_tool("Small Catalog", category)
        status = create_ivfflat_index()
        assert status is None  # below MIN_ROWS_FOR_IVFFLAT -> no-op
        assert not _index_on_tools_table()

    def test_force_creates_and_drop_removes(self, category, make_tool):
        make_tool("Forced", category)
        status = create_ivfflat_index(force=True, lists=10)
        assert status and "lists=10" in status
        assert _index_on_tools_table()
        assert drop_ivfflat_index() is True
        assert not _index_on_tools_table()


@pytest.mark.django_db(transaction=True)  # see TestIvfflatLifecycle
class TestRecallRegression:
    def test_ann_index_does_not_drop_candidates(self, api_user, category, make_tool):
        """THE regression: with the ANN index present and 4 tiny rows, a
        vector-ordered query must still return every eligible row (thanks to
        raised probes inside ivfflat_probes)."""
        user, _ = api_user
        anchor = make_tool("Anchor", category, embedding=unit_vector(0))
        Bookmark.objects.create(user=user, tool=anchor)
        Interaction.objects.create(user=user, tool=anchor, type="bookmark", weight=3.0)

        _candidates = [
            make_tool(f"Cand {i}", category, embedding=unit_vector(i % 5))
            for i in range(4)
        ]
        create_ivfflat_index(force=True, lists=10)  # worst case: many empty lists
        try:
            with ivfflat_probes():
                rows = list(
                    Tool.objects.filter(embedding__isnull=False)
                    .exclude(pk=anchor.pk)
                    .annotate(distance=CosineDistance("embedding", unit_vector(0)))
                    .order_by("distance")
                )
            assert len(rows) == 4

            ranked, strategy = recommend_for_user(user, limit=10, diversify=False)
            assert strategy == "personalised"
            assert len(ranked) == 4  # nothing silently dropped
        finally:
            drop_ivfflat_index()
