"""Celery wrappers for preference-vector jobs (see catalog/celery_tasks.py)."""
from config.celery import app
from .tasks import run_for_all_users, run_for_user


@app.task(name="recommendations.recompute_all_preference_vectors")
def recompute_all_preference_vectors_task() -> None:
    run_for_all_users()


@app.task(name="recommendations.recompute_preference_vector")
def recompute_preference_vector_task(user_id: int) -> None:
    run_for_user(user_id)
