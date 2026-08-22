"""
Multi-model chat comparison data model (Section 4.3).

A ChatTurn is ONE user prompt fanned out to N models; each ModelResponse is
one model's answer to that turn, persisted with the measurements that make
comparison meaningful (latency, token count, error state).
"""
import uuid

from django.conf import settings
from django.db import models


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_sessions"
    )
    title = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"session {self.id.hex[:8]} ({self.user.username})"


class ChatTurn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE, related_name="turns")
    prompt = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"turn {self.id.hex[:8]}: {self.prompt[:40]}"


class ModelResponse(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        STREAMING = "streaming", "Streaming"
        DONE = "done", "Done"
        ERROR = "error", "Error"

    id = models.BigAutoField(primary_key=True)
    turn = models.ForeignKey(ChatTurn, on_delete=models.CASCADE, related_name="responses")
    model_id = models.CharField(max_length=100, help_text="Registry id, e.g. 'gpt-4o-mini'")
    provider = models.CharField(max_length=50)
    model_name = models.CharField(max_length=150, blank=True, default="",
                                  help_text="Vendor-side model identifier actually called.")
    response_text = models.TextField(blank=True, default="")
    token_count = models.PositiveIntegerField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("turn", "model_id"), name="unique_turn_model_response")
        ]

    def __str__(self) -> str:
        return f"{self.model_id} @ {self.turn_id.hex[:8]}: {self.status}"
