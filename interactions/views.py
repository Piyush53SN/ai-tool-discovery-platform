"""Bookmarks & reviews API."""
from rest_framework import mixins, status, viewsets
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .models import Bookmark, Review
from .serializers import (
    BookmarkCreateSerializer,
    BookmarkSerializer,
    ReviewCreateSerializer,
    ReviewSerializer,
)
from .services import delete_review, remove_bookmark, set_bookmark, submit_review


class BookmarkViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """POST/GET/DELETE /api/bookmarks/ (Section 6.4).

    Every create/delete also maintains the weighted bookmark Interaction row
    (services.set_bookmark / remove_bookmark) — the recommender's input.
    """

    serializer_class = BookmarkSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "writes"

    def get_queryset(self):
        return (
            Bookmark.objects.filter(user=self.request.user)
            .select_related("tool", "tool__category")
            .prefetch_related("tool__tags")
        )

    def get_serializer_context(self):
        # Tools nested in the bookmark list keep their "is_bookmarked" badge.
        context = super().get_serializer_context()
        context["bookmarked_ids"] = bookmarked_ids_lazy(self.request.user)
        return context

    def create(self, request, *args, **kwargs):
        input_serializer = BookmarkCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        tool = input_serializer.validated_data["tool"]
        bookmark, created = set_bookmark(request.user, tool)
        return Response(
            BookmarkSerializer(bookmark, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        remove_bookmark(instance)


def bookmarked_ids_lazy(user):
    from .services import bookmarked_tool_ids

    return bookmarked_tool_ids(user)


class ReviewViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """POST /api/reviews/ + GET /api/reviews/?tool={slug} + DELETE own review.

    Creating/updating a review maintains the tool's denormalised rating stats
    and the weighted review Interaction row in one transaction.
    """

    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticatedOrReadOnly]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "writes"

    def get_queryset(self):
        qs = Review.objects.select_related("tool", "user")
        # Object actions operate on the whole table so ownership is enforced
        # (with a 403) rather than masked by a queryset-filtered 404.
        if self.action in ("retrieve", "destroy", "update", "partial_update"):
            return qs
        # ?tool=<slug> -> that tool's public review thread (readable by anyone)
        tool_slug = self.request.query_params.get("tool")
        if tool_slug:
            return qs.filter(tool__slug=tool_slug).order_by("-created_at")
        # otherwise -> the current user's own reviews
        if not self.request.user.is_authenticated:
            return qs.none()
        return qs.filter(user=self.request.user).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        input_serializer = ReviewCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        tool = input_serializer.validated_data["tool"]
        rating = input_serializer.validated_data["rating"]
        comment = input_serializer.validated_data["comment"]
        # One review per (user, tool): a resubmit is an update -> 200, not 201.
        review, created = submit_review(request.user, tool, rating, comment)
        return Response(
            ReviewSerializer(review).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        if instance.user_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You can only delete your own reviews.")
        delete_review(instance)
