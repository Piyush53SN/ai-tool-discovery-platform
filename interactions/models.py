"""
Bookmarks, reviews and the raw interaction log feeding the recommender.

The `Interaction` table is the single source of behavioural signal. Each row
carries a weight reflecting how strongly the event expresses preference:

    view      -> 1.0   (weak: might be curiosity)
    bookmark  -> 3.0   (strong: explicit "I want this")
    review    -> 4.0   (strongest: took time to judge)
    review with rating >= 4 -> 5.0 (enthusiastic review, extra bump)

recompute_preference_vector() (recommendations/services.py) turns these rows
into the user's cached Profile.preference_vector.
"""
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class InteractionType(models.TextChoices):
    VIEW = "view", "View"
    BOOKMARK = "bookmark", "Bookmark"
    REVIEW = "review", "Review"


# Weights are deliberately plain data so they can be tuned (and asserted
# against in tests) without touching business logic.
INTERACTION_WEIGHTS: dict[str, float] = {
    InteractionType.VIEW: 1.0,
    InteractionType.BOOKMARK: 3.0,
    InteractionType.REVIEW: 4.0,
}
# Extra weight added to a review interaction when the rating is >= 4.
ENTHUSIASTIC_REVIEW_BONUS = 1.0


def review_weight(rating: int) -> float:
    """Weight of a review interaction: 4.0, bumped to 5.0 when rating >= 4."""
    weight = INTERACTION_WEIGHTS[InteractionType.REVIEW]
    if rating >= 4:
        weight += ENTHUSIASTIC_REVIEW_BONUS
    return weight


class Interaction(models.Model):
    """Append-mostly behavioural log (the recommender's raw input)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="interactions"
    )
    tool = models.ForeignKey("catalog.Tool", on_delete=models.CASCADE, related_name="interactions")
    type = models.CharField(max_length=12, choices=InteractionType.choices)
    weight = models.FloatField(help_text="Signal strength used by the preference vector")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["user", "type"]),
            models.Index(fields=["tool", "type"]),
        ]

    def __str__(self) -> str:
        return f"{self.user} {self.type} -> {self.tool}"


class Bookmark(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bookmarks"
    )
    tool = models.ForeignKey("catalog.Tool", on_delete=models.CASCADE, related_name="bookmarked_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tool"], name="unique_user_tool_bookmark")
        ]
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user} bookmarked {self.tool}"


class Review(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews"
    )
    tool = models.ForeignKey("catalog.Tool", on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tool"], name="unique_user_tool_review")
        ]
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.user} rated {self.tool} {self.rating}/5"
