"""
Test settings — selected via DJANGO_SETTINGS_MODULE in pytest.ini.

pytest-django bootstraps Django *before* importing the root conftest, so
environment defaults for the test run must live here (they execute before
config.settings is imported). Real env vars always win (CI passes its own
DATABASE_URL for the service-container Postgres).

Test profile:
  * EMBEDDING_BACKEND=hash     -> deterministic, offline, instant vectors
  * EMBEDDING_DISPATCH=sync    -> signal-triggered jobs run inline (no races)
  * TESTING=1                  -> throttling effectively disabled
"""
import os

os.environ.setdefault("EMBEDDING_BACKEND", "hash")
os.environ.setdefault("EMBEDDING_DISPATCH", "sync")
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres@127.0.0.1:5432/aitools")

from .settings import *  # noqa: E402,F401,F403 (intentional re-export)
