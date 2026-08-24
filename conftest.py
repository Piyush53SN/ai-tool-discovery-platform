"""
Shared pytest fixtures.

Environment defaults for the run are set in config/settings_test.py (they
must execute before config.settings is imported — pytest-django bootstraps
Django before this conftest's body runs). The test profile runs:

  * against the real PostgreSQL + pgvector instance (CI provides one via a
    service container; locally DATABASE_URL points at it),
  * with the deterministic `hash` embedding backend (no network, no torch),
  * with EMBEDDING_DISPATCH=sync so signal-triggered jobs are observable and
    race-free inside tests (production always dispatches off-path),
  * with throttling disabled (tests hammer endpoints on purpose).
"""
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.models import Category, Tag, Tool

EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Embedding helpers — small, hand-checkable vectors in 384-dim space.
# Axis-aligned unit vectors make cosine similarity exact on paper:
#   sim(unit(i), unit(j)) = 1 if i == j else 0
# ---------------------------------------------------------------------------
def unit_vector(axis: int, dim: int = EMBEDDING_DIM) -> list[float]:
    vec = [0.0] * dim
    vec[axis % dim] = 1.0
    return vec


def scaled_vector(x: float, y: float, dim: int = EMBEDDING_DIM) -> list[float]:
    """Unit vector in the span of the first two axes: [x, y, 0, ...]."""
    vec = [0.0] * dim
    vec[0], vec[1] = x, y
    return vec


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def api_user(db, django_user_model):
    """Plain authenticated user + client pair."""
    user = django_user_model.objects.create_user(
        username="alice", password="strong-pass-1"
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return user, client


@pytest.fixture
def category(db) -> Category:
    return Category.objects.create(name="Writing", slug="writing")


@pytest.fixture
def make_category(db):
    def _make(name: str) -> Category:
        cat, _ = Category.objects.get_or_create(slug=name.lower(), defaults={"name": name})
        return cat

    return _make


@pytest.fixture
def make_tag(db):
    def _make(name: str) -> Tag:
        tag, _ = Tag.objects.get_or_create(slug=name.lower(), defaults={"name": name})
        return tag

    return _make


@pytest.fixture
def make_tool(db):
    """Create a tool with an EXPLICIT embedding.

    The vector is written with queryset.update() after creation so the
    post_save signal (sync in tests) hashes the real text first, and our
    explicit vector + fresh timestamp then overwrite it — leaving the row
    non-stale, i.e. no later signal re-embeds it.
    """

    def _make(
        name: str,
        category: Category,
        embedding: list[float] | None = None,
        tags: list[Tag] | None = None,
        pricing: str = "freemium",
        description: str | None = None,
    ) -> Tool:
        tool = Tool.objects.create(
            name=name,
            slug=name.lower().replace(" ", "-"),
            description=description or f"{name} does one thing well.",
            url=f"https://example.com/{name.lower().replace(' ', '-')}",
            category=category,
            pricing_tier=pricing,
        )
        if tags:
            tool.tags.set(tags)
        if embedding is not None:
            Tool.objects.filter(pk=tool.pk).update(
                embedding=embedding, embedding_updated_at=timezone.now()
            )
            tool.refresh_from_db()
        return tool

    return _make
