"""GET /api/recommendations/ — ranked tools for the current user (Section 6.5)."""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RecommendationResponseSerializer
from .services import preference_is_fresh, recommend_for_user


class RecommendationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Params: ?limit=1..24, ?diversify=true|false
        try:
            limit = max(1, min(24, int(request.query_params.get("limit", 0) or 0)) or 8)
        except (TypeError, ValueError):
            limit = 8
        diversify_param = request.query_params.get("diversify")
        diversify = None if diversify_param is None else diversify_param.lower() in ("1", "true", "yes")

        recommendations, strategy = recommend_for_user(
            request.user, limit=limit, diversify=diversify
        )
        payload = {
            "count": len(recommendations),
            "strategy": strategy,
            "preference_vector_ready": preference_is_fresh(request.user),
            "diversify": strategy == "personalised_diverse",
            "results": [
                {
                    "tool": r.tool,
                    "similarity": r.similarity,
                    "score": r.score,
                    "reason": r.reason,
                }
                for r in recommendations
            ],
        }
        return Response(RecommendationResponseSerializer(payload).data)
