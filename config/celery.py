"""
Celery wiring (optional at runtime).

Enable with USE_CELERY=1 plus a running broker (see .env.example). When Celery
is disabled, embedding jobs fall back to an in-process background thread pool
so the app still never computes embeddings on the request path
(see catalog/tasks.py).
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("aitools")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
