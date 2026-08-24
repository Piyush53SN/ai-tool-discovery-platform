"""API-level tests for /api/recommendations/."""
import pytest

from conftest import unit_vector
from interactions.models import Bookmark, Interaction

pytestmark = pytest.mark.django_db

RECS_URL = "/api/recommendations/"


class TestRecommendationsEndpoint:
    def test_requires_authentication(self, api):
        assert api.get(RECS_URL).status_code == 401

    def test_payload_shape(self, api_user, category, make_tool):
        user, client = api_user
        for i in range(3):
            make_tool(f"Popular {i}", category)
        response = client.get(RECS_URL)
        assert response.status_code == 200
        body = response.json()
        assert body["strategy"] == "cold_start_popularity"
        assert body["count"] == 3
        item = body["results"][0]
        assert set(item) >= {"tool", "similarity", "score", "reason"}
        assert item["tool"]["name"].startswith("Popular")

    def test_strategy_switches_after_first_interaction(self, api_user, category, make_tool):
        user, client = api_user
        anchor = make_tool("Anchor", category, embedding=unit_vector(0))
        make_tool("Candidate", category, embedding=unit_vector(0))
        Bookmark.objects.create(user=user, tool=anchor)
        Interaction.objects.create(user=user, tool=anchor, type="bookmark", weight=3.0)

        body = client.get(RECS_URL).json()
        assert body["strategy"].startswith("personalised")
        assert body["preference_vector_ready"] is True
        names = [r["tool"]["name"] for r in body["results"]]
        assert "Anchor" not in names  # bookmarked tools never recommended

    def test_limit_and_diversify_params(self, api_user, category, make_tool):
        user, client = api_user
        for i in range(6):
            make_tool(f"Tool {i}", category)
        body = client.get(RECS_URL, {"limit": 3}).json()
        assert body["count"] == 3
        body = client.get(RECS_URL, {"limit": 99}).json()
        assert body["count"] <= 24  # hard cap

    def test_reason_and_score_present(self, api_user, category, make_tool):
        user, client = api_user
        make_tool("Explained", category)
        item = client.get(RECS_URL).json()["results"][0]
        assert item["reason"]
        assert isinstance(item["score"], float)
