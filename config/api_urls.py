"""
API routing (Section 7). All endpoints live under /api/.

    POST /api/auth/register/            RegisterView
    POST /api/auth/token/               JWT obtain (SimpleJWT)
    POST /api/auth/token/refresh/       JWT refresh
    GET|PATCH /api/auth/me/             Profile + onboarding tags
    GET  /api/tools/                    list + search + facets + ordering
    GET  /api/tools/{slug}/             detail (+ semantic neighbours)
    POST /api/tools/compare/            2–4 tools side-by-side matrix
    POST /api/tools/{slug}/view/        view tracking (authenticated)
    POST|GET|DELETE /api/bookmarks/     bookmark CRUD
    POST|GET /api/reviews/              reviews (+ ?tool= public thread)
    GET  /api/recommendations/          personalised ranking
    GET  /api/categories/ , /api/tags/  facet metadata
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.serializers import CustomTokenObtainPairSerializer
from accounts.views import MeView, RegisterView
from catalog.views import CategoryViewSet, TagViewSet, ToolViewSet
from chat.views import ChatModelsView, TurnDetailView, TurnStreamView, TurnView
from interactions.views import BookmarkViewSet, ReviewViewSet
from recommendations.views import RecommendationListView


class ThrottledTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


router = DefaultRouter()
router.register("tools", ToolViewSet, basename="tool")
router.register("categories", CategoryViewSet, basename="category")
router.register("tags", TagViewSet, basename="tag")
router.register("bookmarks", BookmarkViewSet, basename="bookmark")
router.register("reviews", ReviewViewSet, basename="review")

urlpatterns = [
    # Auth
    path("auth/register/", RegisterView.as_view(), name="auth-register"),
    path("auth/token/", ThrottledTokenObtainPairView.as_view(), name="token-obtain-pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    # Recommendations
    path("recommendations/", RecommendationListView.as_view(), name="recommendations"),
    # Multi-model chat comparison (Section 4)
    path("chat/models/", ChatModelsView.as_view(), name="chat-models"),
    path("chat/turns/", TurnView.as_view(), name="chat-turn-create"),
    path(
        "chat/turns/<uuid:turn_id>/stream/<str:model_id>/",
        TurnStreamView.as_view(),
        name="chat-turn-stream",
    ),
    path("chat/turns/<uuid:turn_id>/", TurnDetailView.as_view(), name="chat-turn-detail"),
    # Routers
    path("", include(router.urls)),
]
