"""Model-level behaviour: constraints, denormalisation hooks, staleness."""
import pytest
from django.db import IntegrityError

from catalog.embeddings import HashingEmbedder, get_embedder
from catalog.models import Category, Tool

pytestmark = pytest.mark.django_db


class TestCategoryAndTag:
    def test_str(self, category):
        assert str(category) == "Writing"

    def test_unique_slugs(self, category):
        with pytest.raises(IntegrityError):
            Category.objects.create(name="Writing Again", slug="writing")


class TestTool:
    def test_str_and_defaults(self, category):
        tool = Tool.objects.create(
            name="My Tool", slug="my-tool", description="d", url="https://x.dev",
            category=category,
        )
        assert str(tool) == "My Tool"
        assert tool.pricing_tier == Tool.PricingTier.FREEMIUM
        assert tool.avg_rating == 0
        assert tool.bookmark_count == 0

    def test_embedding_input_includes_tags(self, category, make_tool, make_tag):
        t1, t2 = make_tag("seo"), make_tag("copywriting")
        tool = make_tool("Jasper", category, tags=[t1, t2])
        text = tool.embedding_input()
        assert text.startswith("Jasper.")
        # Tags render alphabetically (model ordering), order-insensitive check:
        assert "Tags:" in text
        assert set(["seo", "copywriting"]) <= set(text.split("Tags: ")[1].split(", "))

    def test_embedding_generated_off_request_via_signal(self, category):
        """Creating a tool enqueues (sync in tests) an embedding job — the
        write itself never computes the vector inline."""
        tool = Tool.objects.create(
            name="Embed Me", slug="embed-me", description="text about things",
            url="https://x.dev", category=category,
        )
        tool.refresh_from_db()
        assert tool.embedding is not None
        assert len(tool.embedding) == 384
        assert tool.embedding_updated_at is not None

    def test_embedding_is_stale_flag(self, category):
        tool = Tool.objects.create(
            name="Stale", slug="stale", description="d", url="https://x.dev",
            category=category,
        )
        tool.refresh_from_db()
        assert not tool.embedding_is_stale  # fresh from the sync job
        # A text edit bumps updated_at -> stale -> signal re-embeds.
        tool.description = "changed description entirely"
        tool.save()
        fresh = Tool.objects.get(pk=tool.pk)
        assert not fresh.embedding_is_stale  # re-embedded by the sync job

    def test_counter_updates_do_not_mark_embedding_stale(self, category):
        from interactions.services import recompute_rating_stats

        tool = Tool.objects.create(
            name="Counters", slug="counters", description="d", url="https://x.dev",
            category=category,
        )
        before = Tool.objects.get(pk=tool.pk).embedding_updated_at
        assert before is not None
        tool.avg_rating = 4.5
        tool.rating_count = 7
        recompute_rating_stats(tool)  # uses update_fields without updated_at
        after = Tool.objects.get(pk=tool.pk)
        assert not after.embedding_is_stale


class TestHashingEmbedder:
    def test_shape_and_unit_norm(self):
        emb = HashingEmbedder(dim=384)
        vectors = emb.encode(["hello world", "hello world", "something else"])
        assert vectors.shape == (3, 384)
        assert vectors[0].tolist() == vectors[1].tolist()  # deterministic
        assert vectors[0].tolist() != vectors[2].tolist()

    def test_overlapping_text_is_more_similar(self):
        import numpy as np

        emb = HashingEmbedder(dim=384)
        a, b, c = emb.encode(["ai writing assistant for blogs",
                              "ai writing assistant for essays",
                              "postgres vector database index"])
        def sim(u, v):
            return float(np.dot(u, v))

        assert sim(a, b) > sim(a, c)

    def test_get_embedder_singleton(self):
        first, second = get_embedder(), get_embedder()
        assert first is second
        assert first.name == "hash"  # TEST env pins the backend
