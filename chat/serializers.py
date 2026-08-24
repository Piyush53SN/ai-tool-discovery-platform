"""Chat serializers."""
from rest_framework import serializers

from . import providers
from .models import ChatTurn, ModelResponse


class ChatModelsSerializer(serializers.Serializer):
    """One registry entry: what it is + whether it's honestly connected."""

    id = serializers.CharField()
    provider = serializers.CharField()
    label = serializers.CharField()
    model_name = serializers.CharField()
    connected = serializers.BooleanField()
    key_source = serializers.CharField()  # global | user | none (Fix 2)


class TurnCreateSerializer(serializers.Serializer):
    """POST /api/chat/turns/ body {prompt, model_ids: [...], conversation_id?}."""

    prompt = serializers.CharField(max_length=4000)
    model_ids = serializers.ListField(
        child=serializers.CharField(max_length=100), min_length=1, max_length=4
    )
    conversation_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_model_ids(self, value: list[str]) -> list[str]:
        known = {spec["id"] for spec in providers.REGISTRY}
        unknown = [m for m in value if m not in known]
        if unknown:
            raise serializers.ValidationError(
                f"Unknown model id(s): {unknown}. See /api/chat/models/."
            )
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Duplicate model ids are pointless.")
        return value


class ModelResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModelResponse
        fields = (
            "model_id", "provider", "model_name", "response_text",
            "token_count", "latency_ms", "status", "error_message",
        )


class TurnDetailSerializer(serializers.Serializer):
    turn_id = serializers.SerializerMethodField()
    conversation_id = serializers.SerializerMethodField()
    prompt = serializers.CharField()
    created_at = serializers.DateTimeField()
    responses = ModelResponseSerializer(many=True)

    def get_turn_id(self, obj: ChatTurn) -> str:
        return str(obj.id)

    def get_conversation_id(self, obj: ChatTurn) -> str:
        return str(obj.session_id)


class ProviderKeySerializer(serializers.Serializer):
    """POST /api/chat/keys/ body {provider, api_key} — BYOK (Fix C).

    The response carries booleans only; a stored key is never echoed back.
    """

    provider = serializers.CharField(max_length=50)
    api_key = serializers.CharField(max_length=4096, write_only=True)

    def validate_provider(self, value: str) -> str:
        known = {spec["provider"] for spec in providers.REGISTRY}
        if value not in known:
            raise serializers.ValidationError(
                f"Unknown provider: {value}. Known: {sorted(known)}"
            )
        return value
