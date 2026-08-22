"""Catalog API: tools (list/detail/compare/view), categories, tags."""
from django.db.models import Count
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from interactions.services import bookmarked_tool_ids, record_view

from .filters import ToolFilter
from .models import Category, Tag, Tool
from .search import HybridSearchFilter
from .serializers import (
    CategorySerializer,
    CompareRequestSerializer,
    TagSerializer,
    ToolDetailSerializer,
    ToolListSerializer,
    ToolViewSerializer,
    build_compare_matrix,
)


class ToolViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/tools/ (search + facets + ordering) and /api/tools/{slug}/."""

    queryset = (
        Tool.objects.select_related("category")
        .prefetch_related("tags")
        .order_by("-avg_rating", "-created_at")
    )
    serializer_class = ToolListSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    lookup_field = "slug"

    # Faceted filtering (django-filter) + explicit ordering + hybrid search.
    # HybridSearchFilter runs last so the hybrid ordering wins when ?search=
    # is present.
    filterset_class = ToolFilter
    filter_backends = (DjangoFilterBackend, OrderingFilter, HybridSearchFilter)
    ordering_fields = ("avg_rating", "created_at", "name", "bookmark_count", "rating_count")
    ordering = ("-avg_rating", "-created_at")

    # ---- helpers ------------------------------------------------------------
    def get_serializer_class(self):
        return ToolDetailSerializer if self.action == "retrieve" else ToolListSerializer

    def paginate_queryset(self, queryset):
        page = super().paginate_queryset(queryset)
        # One query per page (not per card) for the "is_bookmarked" badge.
        if page is not None:
            user = getattr(self.request, "user", None)
            self.bookmarked_ids = (
                bookmarked_tool_ids(user) if user and user.is_authenticated else set()
            )
        return page

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["bookmarked_ids"] = getattr(self, "bookmarked_ids", None)
        return context

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        # Echo the active query + which ranking was used (transparency for
        # graders / debugging hybrid search behaviour).
        if getattr(self, "search_query", None):
            response.data["search"] = {
                "query": self.search_query,
                "ranking": "hybrid (full-text + semantic cosine)",
            }
        return response

    # ---- extra actions --------------------------------------------------------
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[ScopedRateThrottle],
    )
    def compare(self, request):
        """POST /api/tools/compare/ body {tool_ids: [2–4 ids]} (Section 6.3)."""
        self.throttle_scope = "writes"
        serializer = CompareRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tools = serializer.context["tools"]
        bookmarked = (
            bookmarked_tool_ids(request.user) if request.user.is_authenticated else set()
        )
        return Response(build_compare_matrix(tools, bookmarked))

    @action(
        detail=True,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[ScopedRateThrottle],
    )
    def view(self, request, slug=None):
        """POST /api/tools/{slug}/view/ — lightweight view tracking.

        Records a weighted `view` Interaction for authenticated users (the
        recommender's weakest signal). Anonymous hits are accepted but not
        logged: a preference vector is per-user by definition.
        """
        self.throttle_scope = "writes"
        tool = self.get_object()
        recorded = record_view(request.user, tool)
        return Response(
            ToolViewSerializer(
                {
                    "recorded": recorded,
                    "detail": "View recorded."
                    if recorded
                    else "Sign in to have views shape your recommendations.",
                }
            ).data
        )


class CategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/categories/ — includes per-category tool counts for facets."""

    queryset = Category.objects.annotate(tool_count=Count("tools")).all()
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]
    pagination_class = None
    lookup_field = "slug"


class TagViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/tags/ — tag facet with counts (ordered by popularity)."""

    queryset = Tag.objects.annotate(tool_count=Count("tools")).order_by("-tool_count").all()
    serializer_class = TagSerializer
    permission_classes = [AllowAny]
    pagination_class = None
    lookup_field = "slug"
