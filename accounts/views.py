"""Registration + /api/auth/me/ (profile & onboarding tags)."""
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .serializers import (
    MeSerializer,
    RegisterSerializer,
    UpdateOnboardingTagsSerializer,
)


class RegisterView(APIView):
    """POST /api/auth/register/ — returns JWT pair + user immediately."""

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        from rest_framework_simplejwt.tokens import RefreshToken

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "user": MeSerializer(user).data,
                "tokens": {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
            },
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    """GET /api/auth/me/ — identity + recommender state.

    PATCH /api/auth/me/ — update onboarding tags (the cold-start quiz),
    accepted at any time; the next recommendation request reflects them.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        serializer = UpdateOnboardingTagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        profile = request.user.profile
        profile.onboarding_tags.set(serializer.validated_data["onboarding_tags"])
        return Response(MeSerializer(request.user).data)
