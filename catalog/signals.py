"""
Signals wiring embedding generation to tool lifecycle events (Section 6.5.1).

post_save / m2m_changed on Tool -> dispatch_tool_embedding() -> background
worker. The dispatch is skipped when the row is not stale, which both avoids
recomputing on unrelated updates (denormalised counters) and terminates the
save -> signal -> save cycle.
"""
import logging

from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .models import Tool
from .tasks import dispatch_tool_embedding

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Tool)
def tool_saved(sender, instance: Tool, created: bool, **kwargs) -> None:
    if created or instance.embedding_is_stale:
        dispatch_tool_embedding(instance.pk)
    # else: a counter/embedding-only write — nothing semantic changed.


@receiver(m2m_changed, sender=Tool.tags.through)
def tool_tags_changed(sender, instance, action: str, **kwargs) -> None:
    # Tags are part of the embedding input, and M2M changes do not bump
    # `updated_at`, so dispatch on every add/remove/clear. No loop risk: the
    # embedding job never touches the tags relation.
    if action in ("post_add", "post_remove", "post_clear") and isinstance(instance, Tool):
        dispatch_tool_embedding(instance.pk)
