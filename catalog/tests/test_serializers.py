"""Serializer-level tests: registration, compare, tool badges."""
import pytest
from django.contrib.auth import get_user_model

from catalog.serializers import (
    CompareRequestSerializer,
    ToolListSerializer,
    build_compare_matrix,
)

pytestmark = pytest.mark.django_db

User = get_user_model()


class TestRegisterSerializer:
    def payload(self, **overrides):
        base = {"username": "newuser", "password": "very-secret-99"}
        base.update(overrides)
        return base

    def test_creates_user_and_profile_with_tags(self, make_tag):
        from accounts.serializers import RegisterSerializer

        make_tag("seo")
        make_tag("writing-assistant")
        serializer = RegisterSerializer(
            data=self.payload(onboarding_tags=["seo", "writing-assistant"])
        )
        assert serializer.is_valid(), serializer.errors
        user = serializer.save()
        assert user.profile.onboarding_tags.count() == 2

    def test_duplicate_username_rejected(self):
        from accounts.serializers import RegisterSerializer

        User.objects.create_user(username="taken", password="whatever-pass-1")
        serializer = RegisterSerializer(data=self.payload(username="taken"))
        assert not serializer.is_valid()
        assert "username" in serializer.errors

    @pytest.mark.parametrize("bad", ["short", "12345678", "newuser123"])
    def test_weak_passwords_rejected(self, bad):
        from accounts.serializers import RegisterSerializer

        serializer = RegisterSerializer(data=self.payload(password=bad))
        assert not serializer.is_valid()
        assert "password" in serializer.errors


class TestCompareSerializers:
    def test_two_to_four_ids_required(self):
        for ids in ([1], [1, 2, 3, 4, 5]):
            serializer = CompareRequestSerializer(data={"tool_ids": ids})
            assert not serializer.is_valid()

    def test_unknown_ids_rejected(self, category, make_tool):
        real = make_tool("Real", category)
        serializer = CompareRequestSerializer(data={"tool_ids": [real.pk, 9999]})
        assert not serializer.is_valid()
        assert "tool_ids" in serializer.errors

    def test_matrix_shape(self, category, make_category, make_tool):
        a = make_tool("Alpha", category, pricing="free")
        b = make_tool("Beta", make_category("video-audio"), pricing="paid")
        matrix = build_compare_matrix([a, b], bookmarked_ids={a.pk})
        assert [t["name"] for t in matrix["tools"]] == ["Alpha", "Beta"]
        by_key = {row["attribute"]: row for row in matrix["rows"]}
        assert by_key["pricing"]["values"] == ["Free", "Paid"]
        assert by_key["bookmarked"]["values"] == [True, False]
        assert len(by_key["category"]["values"]) == 2


class TestToolListSerializer:
    def test_is_bookmarked_from_context_ids(self, category, make_tool, api_user):
        user, _ = api_user
        tool = make_tool("Flagged", category)
        data = ToolListSerializer(
            tool, context={"bookmarked_ids": {tool.pk}}
        ).data
        assert data["is_bookmarked"] is True

        data = ToolListSerializer(tool, context={"bookmarked_ids": set()}).data
        assert data["is_bookmarked"] is False
