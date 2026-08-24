"""Serializers for /api/recommendations/."""
from rest_framework import serializers

from catalog.serializers import ToolListSerializer


class RecommendationSerializer(serializers.Serializer):
    """One ranked item: the tool card + explainability metadata."""

    tool = ToolListSerializer(read_only=True)
    similarity = serializers.FloatField(read_only=True)
    score = serializers.FloatField(read_only=True)
    reason = serializers.CharField(read_only=True)


class RecommendationResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)
    strategy = serializers.CharField(read_only=True)
    strategy_display = serializers.SerializerMethodField()
    preference_vector_ready = serializers.BooleanField(read_only=True)
    diversify = serializers.BooleanField(read_only=True)
    results = RecommendationSerializer(many=True, read_only=True)

    STRATEGY_LABELS = {
        "personalised": "Personalised — cosine similarity to your taste vector",
        "personalised_diverse": "Personalised + diversity re-rank (MMR)",
        "cold_start_tags": "Cold start — onboarding interests x popularity",
        "cold_start_popularity": "Cold start — popular right now",
    }

    def get_strategy_display(self, obj) -> str:
        return self.STRATEGY_LABELS.get(obj.get("strategy"), obj.get("strategy", ""))
