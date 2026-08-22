"""Serializers for the catalog: categories, tags, tools, compare payload."""
from rest_framework import serializers

from interactions.services import bookmarked_tool_ids
from recommendations.services import similar_tools

from .models import Category, Tag, Tool


class CategorySerializer(serializers.ModelSerializer):
    tool_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Category
        fields = ("id", "name", "slug", "description", "tool_count")


class CategorySlimSerializer(serializers.ModelSerializer):
    """Nested inside tool payloads (no facet counts there)."""

    class Meta:
        model = Category
        fields = ("id", "name", "slug")


class TagSerializer(serializers.ModelSerializer):
    tool_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Tag
        fields = ("id", "name", "slug", "tool_count")


class TagSlimSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ("id", "name", "slug")


class ToolListSerializer(serializers.ModelSerializer):
    """Card-shaped payload for grids / lists."""

    category = CategorySlimSerializer(read_only=True)
    tags = TagSlimSerializer(many=True, read_only=True)
    pricing_display = serializers.CharField(source="get_pricing_tier_display", read_only=True)
    is_bookmarked = serializers.SerializerMethodField()
    embedding_ready = serializers.SerializerMethodField()

    class Meta:
        model = Tool
        fields = (
            "id",
            "slug",
            "name",
            "description",
            "url",
            "category",
            "tags",
            "pricing_tier",
            "pricing_display",
            "avg_rating",
            "rating_count",
            "bookmark_count",
            "is_bookmarked",
            "embedding_ready",
            "created_at",
        )

    def get_is_bookmarked(self, obj) -> bool:
        # Populated in bulk by the view (context["bookmarked_ids"]) to avoid
        # a query per card; falls back to a single check otherwise.
        bookmarked = self.context.get("bookmarked_ids")
        if bookmarked is None:
            request = self.context.get("request")
            if not request or not request.user.is_authenticated:
                return False
            bookmarked = bookmarked_tool_ids(request.user)
        return obj.pk in bookmarked

    def get_embedding_ready(self, obj) -> bool:
        return obj.embedding is not None


class ToolDetailSerializer(ToolListSerializer):
    """Detail payload: full text + semantically nearest neighbours."""

    similar_tools = serializers.SerializerMethodField()

    class Meta(ToolListSerializer.Meta):
        fields = ToolListSerializer.Meta.fields + ("similar_tools", "updated_at")

    def get_similar_tools(self, obj) -> list[dict]:
        # Nearest neighbours by embedding cosine distance (pgvector `<=>`),
        # computed in SQL. Cards inherit bookmark flags but never recurse.
        neighbours = similar_tools(obj, limit=3)
        return ToolListSerializer(neighbours, many=True, context=self.context).data


# ---------------------------------------------------------------------------
# Compare (Section 6.3) — computed per request, nothing persisted
# ---------------------------------------------------------------------------
class CompareRequestSerializer(serializers.Serializer):
    tool_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        min_length=2,
        max_length=4,
        required=True,
    )

    def validate_tool_ids(self, value: list[int]) -> list[int]:
        found = {
            t.pk: t for t in Tool.objects.filter(pk__in=value)
            .select_related("category")
            .prefetch_related("tags")
        }
        missing = [tid for tid in value if tid not in found]
        if missing:
            raise serializers.ValidationError(
                {"tool_ids": f"Unknown tool id(s): {missing}"}
            )
        # Preserve the client's order, de-duplicated.
        seen, ordered = set(), []
        for tid in value:
            if tid not in seen:
                seen.add(tid)
                ordered.append(tid)
        # Duplicates can shrink the list below the 2-tool minimum
        # (e.g. [5, 5] -> one tool); a 1-column comparison is meaningless.
        if len(ordered) < 2:
            raise serializers.ValidationError(
                {"tool_ids": "Need at least 2 distinct tools to compare."}
            )
        self.context["tools"] = [found[tid] for tid in ordered]
        return ordered


class CompareToolSerializer(serializers.ModelSerializer):
    category = serializers.CharField(source="category.name", read_only=True)
    tags = serializers.SerializerMethodField()

    class Meta:
        model = Tool
        fields = (
            "id",
            "slug",
            "name",
            "pricing_tier",
            "category",
            "tags",
            "avg_rating",
            "rating_count",
            "bookmark_count",
            "url",
            "description",
            "created_at",
        )

    def get_tags(self, obj) -> list[str]:
        return [tag.name for tag in obj.tags.all()]


def build_compare_matrix(tools: list, bookmarked_ids: set[int] | None = None) -> dict:
    """Shape the response as a row-major attribute matrix for a side-by-side
    table: one row per attribute, one value column per tool. The frontend can
    render it directly without pivoting.
    """
    bookmarked_ids = bookmarked_ids or set()

    def row(key: str, label: str, values: list) -> dict:
        return {"attribute": key, "label": label, "values": values}

    rows = [
        row("pricing", "Pricing", [t.get_pricing_tier_display() for t in tools]),
        row("category", "Category", [t.category.name for t in tools]),
        row(
            "tags",
            "Tags",
            [[tag.name for tag in t.tags.all()] for t in tools],
        ),
        row(
            "rating",
            "Avg. rating",
            [
                f"{t.avg_rating:.2f} ({t.rating_count} review{'s' if t.rating_count != 1 else ''})"
                for t in tools
            ],
        ),
        row("bookmarks", "Bookmarks", [t.bookmark_count for t in tools]),
        row("bookmarked", "Saved by you", [t.pk in bookmarked_ids for t in tools]),
        row("website", "Website", [t.url for t in tools]),
        row("description", "Description", [t.description for t in tools]),
    ]
    return {
        "tools": CompareToolSerializer(tools, many=True).data,
        "rows": rows,
    }


class ToolViewSerializer(serializers.Serializer):
    """Response body of POST /api/tools/{id}/view/."""

    recorded = serializers.BooleanField()
    detail = serializers.CharField(read_only=True)
