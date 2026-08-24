"""
User profile: cached preference vector + cold-start onboarding tags.

`preference_vector` is the weighted average of the embeddings of every tool the
user interacted with (see recommendations/services.py). It is recomputed off the
request path whenever new interactions arrive and by a periodic Celery beat
task, then served from this column — the recommendation endpoint itself only
ever reads it.
"""
from django.conf import settings
from django.db import models
from pgvector.django import VectorField


class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    preference_vector = VectorField(dimensions=settings.EMBEDDING_DIM, null=True, blank=True)
    preference_updated_at = models.DateTimeField(null=True, blank=True)
    # Optional picks from an onboarding quiz — seeds the cold-start fallback.
    onboarding_tags = models.ManyToManyField("catalog.Tag", blank=True, related_name="onboarded_users")

    def __str__(self) -> str:
        return f"Profile of {self.user.username}"
