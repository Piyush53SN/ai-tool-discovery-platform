"""API tests for /api/auth/*: register, token, refresh, me."""
import pytest

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/auth/register/"
TOKEN_URL = "/api/auth/token/"
REFRESH_URL = "/api/auth/token/refresh/"
ME_URL = "/api/auth/me/"


class TestRegister:
    def test_register_returns_tokens_and_user(self, api):
        response = api.post(
            REGISTER_URL,
            {"username": "rita", "password": "strong-pass-9",
             "email": "rita@example.com"},
            format="json",
        )
        assert response.status_code == 201
        body = response.json()
        assert body["user"]["username"] == "rita"
        assert body["tokens"]["access"] and body["tokens"]["refresh"]

        # The access token must actually authorise a protected call.
        me = api.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {body['tokens']['access']}")
        assert me.status_code == 200
        assert me.json()["username"] == "rita"

    def test_register_with_onboarding_tags(self, api, make_tag):
        make_tag("seo")
        make_tag("tutoring")
        response = api.post(
            REGISTER_URL,
            {"username": "sam", "password": "strong-pass-9",
             "onboarding_tags": ["seo", "tutoring"]},
            format="json",
        )
        assert response.status_code == 201
        slugs = [t["slug"] for t in response.json()["user"]["onboarding_tags"]]
        assert slugs == ["seo", "tutoring"]

    def test_register_duplicate_username(self, api, django_user_model):
        django_user_model.objects.create_user(username="dupe", password="strong-pass-9")
        response = api.post(
            REGISTER_URL, {"username": "dupe", "password": "strong-pass-9"}, format="json"
        )
        assert response.status_code == 400
        assert "username" in response.json()


class TestTokenEndpoints:
    def test_obtain_pair(self, api, django_user_model):
        django_user_model.objects.create_user(username="tia", password="strong-pass-9")
        response = api.post(
            TOKEN_URL, {"username": "tia", "password": "strong-pass-9"}, format="json"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["access"] and body["refresh"]
        assert body["user"]["username"] == "tia"

    def test_obtain_rejects_bad_password(self, api, django_user_model):
        django_user_model.objects.create_user(username="tia", password="strong-pass-9")
        response = api.post(
            TOKEN_URL, {"username": "tia", "password": "wrong-pass-99"}, format="json"
        )
        assert response.status_code == 401

    def test_refresh_flow(self, api, django_user_model):
        django_user_model.objects.create_user(username="tia", password="strong-pass-9")
        tokens = api.post(
            TOKEN_URL, {"username": "tia", "password": "strong-pass-9"}, format="json"
        ).json()
        response = api.post(REFRESH_URL, {"refresh": tokens["refresh"]}, format="json")
        assert response.status_code == 200
        assert response.json()["access"]


class TestMe:
    def test_requires_authentication(self, api):
        assert api.get(ME_URL).status_code == 401

    def test_reports_recommender_state(self, api_user):
        user, client = api_user
        body = client.get(ME_URL).json()
        assert body["username"] == "alice"
        assert body["has_preference_vector"] is False
        assert body["interaction_summary"] == {}

    def test_patch_onboarding_tags(self, api_user, make_tag):
        user, client = api_user
        make_tag("seo")
        make_tag("tutoring")
        response = client.patch(
            ME_URL, {"onboarding_tags": ["seo", "tutoring"]}, format="json"
        )
        assert response.status_code == 200
        assert [t["slug"] for t in response.json()["onboarding_tags"]] == ["seo", "tutoring"]
        assert user.profile.onboarding_tags.count() == 2
