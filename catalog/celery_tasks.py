"""
Celery task wrappers (imported lazily only when Celery dispatch is enabled).

Keeping these in a separate module means the Celery app is never instantiated
in deployments that run without a broker. The beat schedule in settings.py
refers to recommendations.tasks.recompute_all_preference_vectors, defined the
same way there.
"""
from config.celery import app

from .tasks import batch_generate_embeddings, generate_tool_embedding


@app.task(name="catalog.generate_tool_embedding")
def generate_tool_embedding_task(tool_id: int) -> None:
    generate_tool_embedding(tool_id)


@app.task(name="catalog.batch_generate_embeddings")
def batch_generate_embeddings_task(tool_ids: list[int]) -> None:
    batch_generate_embeddings(tool_ids)


@app.task(name="catalog.verify_tool_links")
def verify_tool_links_task(limit: int = 50) -> dict:
    """Periodic link verification (Section 3.2) — see catalog/verification.py."""
    from .verification import verify_due_tools

    return verify_due_tools(limit=limit)
