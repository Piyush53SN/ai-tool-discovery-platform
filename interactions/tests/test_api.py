"""API + service tests for bookmarks and reviews."""
import pytest

from interactions.models import Bookmark, Interaction, Review

pytestmark = pytest.mark.django_db

BOOKMARKS_URL = "/api/bookmarks/"
REVIEWS_URL = "/api/reviews/"


class TestBookmarkAPI:
    def test_create_list_destroy(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Keeper", category)

        created = client.post(BOOKMARKS_URL, {"tool_id": tool.pk}, format="json")
        assert created.status_code == 201
        bookmark_id = created.json()["id"]
        assert created.json()["tool"]["name"] == "Keeper"

        listed = client.get(BOOKMARKS_URL)
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        # Denormalised counter + interaction log row both maintained.
        tool.refresh_from_db()
        assert tool.bookmark_count == 1
        assert Interaction.objects.filter(
            user=user, tool=tool, type="bookmark", weight=3.0
        ).exists()

        deleted = client.delete(f"{BOOKMARKS_URL}{bookmark_id}/")
        assert deleted.status_code == 204
        tool.refresh_from_db()
        assert tool.bookmark_count == 0
        assert not Interaction.objects.filter(user=user, tool=tool, type="bookmark").exists()

    def test_duplicate_bookmark_is_idempotent(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Once Only", category)
        first = client.post(BOOKMARKS_URL, {"tool_id": tool.pk}, format="json")
        second = client.post(BOOKMARKS_URL, {"tool_id": tool.pk}, format="json")
        assert (first.status_code, second.status_code) == (201, 200)
        tool.refresh_from_db()
        assert tool.bookmark_count == 1

    def test_requires_authentication(self, api, category, make_tool):
        tool = make_tool("Locked", category)
        assert api.post(BOOKMARKS_URL, {"tool_id": tool.pk}, format="json").status_code == 401
        assert api.get(BOOKMARKS_URL).status_code == 401

    def test_list_shows_only_own_bookmarks(self, api_user, django_user_model,
                                           category, make_tool):
        user, client = api_user
        mine = make_tool("Mine", category)
        other_user = django_user_model.objects.create_user(username="bob", password="x-pass-1234")
        theirs = make_tool("Theirs", category)
        Bookmark.objects.create(user=user, tool=mine)
        Bookmark.objects.create(user=other_user, tool=theirs)
        body = client.get(BOOKMARKS_URL).json()
        assert body["count"] == 1
        assert body["results"][0]["tool"]["name"] == "Mine"


class TestReviewAPI:
    def test_create_updates_denormalised_stats(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Rated", category)
        response = client.post(
            REVIEWS_URL, {"tool_id": tool.pk, "rating": 5, "comment": "superb"}, format="json"
        )
        assert response.status_code == 201
        tool.refresh_from_db()
        assert tool.avg_rating == 5
        assert tool.rating_count == 1
        # rating >= 4 -> interaction weight 4.0 + 1.0 enthusiasm bonus
        assert Interaction.objects.filter(
            user=user, tool=tool, type="review", weight=5.0
        ).exists()

    def test_second_reviewer_aggregates(self, api_user, django_user_model,
                                        category, make_tool):
        user, client = api_user
        tool = make_tool("Multi", category)
        other = django_user_model.objects.create_user(username="carol", password="x-pass-1234")
        Review.objects.create(user=other, tool=tool, rating=3)
        client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 4}, format="json")
        tool.refresh_from_db()
        assert tool.rating_count == 2
        assert tool.avg_rating == 3.5

    def test_resubmit_updates_review_and_reweights(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Edited", category)
        client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 5}, format="json")
        client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 2}, format="json")
        tool.refresh_from_db()
        assert tool.rating_count == 1  # updated, not duplicated
        assert tool.avg_rating == 2
        (interaction,) = Interaction.objects.filter(user=user, tool=tool, type="review")
        assert interaction.weight == 4.0  # no enthusiasm bonus after the edit

    def test_resubmit_returns_200_not_201(self, api_user, category, make_tool):
        """A resubmit is an UPDATE of the (user, tool) review -> 200 OK."""
        user, client = api_user
        tool = make_tool("Status Code", category)
        first = client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 4}, format="json")
        second = client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 3}, format="json")
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["rating"] == 3

    def test_delete_own_review_cleans_up(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Gone", category)
        review = client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 3}, format="json").json()
        assert client.delete(f"{REVIEWS_URL}{review['id']}/").status_code == 204
        tool.refresh_from_db()
        assert tool.rating_count == 0
        assert not Interaction.objects.filter(user=user, tool=tool, type="review").exists()

    def test_cannot_delete_someone_elses_review(self, api_user, django_user_model,
                                                category, make_tool):
        user, client = api_user
        other = django_user_model.objects.create_user(username="mallory", password="x-pass-1234")
        tool = make_tool("Not Yours", category)
        review = Review.objects.create(user=other, tool=tool, rating=4)
        assert client.delete(f"{REVIEWS_URL}{review.pk}/").status_code == 403
        assert Review.objects.filter(pk=review.pk).exists()

    def test_public_thread_endpoint(self, api_user, category, make_tool):
        user, client = api_user
        tool = make_tool("Threaded", category)
        client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": 4, "comment": "good"}, format="json")
        body = client.get(REVIEWS_URL, {"tool": tool.slug}).json()
        assert body["count"] == 1
        assert body["results"][0]["tool_slug"] == tool.slug

    def test_rating_bounds_enforced(self, api_user, category, make_tool):
        _, client = api_user
        tool = make_tool("Bounds", category)
        for bad in (0, 6):
            response = client.post(REVIEWS_URL, {"tool_id": tool.pk, "rating": bad}, format="json")
            assert response.status_code == 400
