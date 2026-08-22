"""Bookmarks & reviews serializers."""
from rest_framework import serializers

from catalog.models import Tool
from catalog.serializers import ToolListSerializer

from .models import Bookmark, Review


class BookmarkSerializer(serializers.ModelSerializer):
    tool = ToolListSerializer(read_only=True)

    class Meta:
        model = Bookmark
        fields = ("id", "tool", "created_at")


class BookmarkCreateSerializer(serializers.Serializer):
    """POST /api/bookmarks/ body {tool_id}."""

    tool_id = serializers.PrimaryKeyRelatedField(
        queryset=Tool.objects.all(), source="tool"
    )


class ReviewSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    tool_name = serializers.CharField(source="tool.name", read_only=True)
    tool_slug = serializers.CharField(source="tool.slug", read_only=True)

    class Meta:
        model = Review
        fields = (
            "id",
            "username",
            "tool_id",
            "tool_name",
            "tool_slug",
            "rating",
            "comment",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")


class ReviewCreateSerializer(serializers.Serializer):
    """POST /api/reviews/ body {tool_id, rating, comment}."""

    tool_id = serializers.PrimaryKeyRelatedField(queryset=Tool.objects.all(), source="tool")
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default="", max_length=4000)
