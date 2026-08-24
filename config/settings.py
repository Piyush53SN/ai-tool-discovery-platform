"""
Django settings for the AI Tool Discovery & Recommendation Platform.

All deployment-specific values are read from environment variables (see
`.env.example`). Secrets are never hardcoded.
"""

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# .env loading (dependency-free)
#
# The README and scripts/run_local.sh write a `.env` file next to manage.py
# and expect it to be honoured. Without this loader Django only saw process
# environment variables, so anything customised in .env silently fell back
# to the defaults below (e.g. the embedded-DB unix-socket DATABASE_URL).
# Semantics: os.environ.setdefault — a *real* exported variable always wins,
# which keeps CI/containers explicit-env workflows authoritative.
# ---------------------------------------------------------------------------
def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_env_file(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Small env helpers
# ---------------------------------------------------------------------------
def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def env_list(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]

# ---------------------------------------------------------------------------
# Core security
# ---------------------------------------------------------------------------
SECRET_KEY = env_str("SECRET_KEY", "django-insecure-dev-key-do-not-use-in-production")

DEBUG = env_bool("DEBUG", True)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1") or ["localhost", "127.0.0.1"]

CSRF_TRUSTED_ORIGINS = [o for o in env_list("CSRF_TRUSTED_ORIGINS") if o]

# Guard rail: refuse to run in "production" mode with obviously broken config.
if not DEBUG:
    if SECRET_KEY.startswith("django-insecure"):
        raise RuntimeError("SECRET_KEY must be set via the environment when DEBUG=False")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # full-text search + pgvector helpers
    # Third party
    "rest_framework",
    "rest_framework_simplejwt",
    "django_filters",
    "corsheaders",
    # Local apps
    "accounts",
    "catalog",
    "interactions",
    "recommendations",
    "chat",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database — PostgreSQL 15+ with pgvector.
# The `vector` extension itself is created by the first migration
# (pgvector.django.VectorExtension), so a privileged DB role is expected.
# ---------------------------------------------------------------------------
DATABASE_URL = env_str("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aitools")

DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=60)}

# dj-database-url ignores `?host=` in the query string (used to point at a
# unix-socket directory, e.g. when running an embedded Postgres locally) —
# wire it through manually.
_qs_host = parse_qs(urlparse(DATABASE_URL).query).get("host")
if _qs_host:
    DATABASES["default"]["HOST"] = _qs_host[0]

DATABASES["default"]["ATOMIC_REQUESTS"] = False

if env_bool("TESTING", False):
    # Persistent connections keep the test database "in use" at teardown.
    DATABASES["default"]["CONN_MAX_AGE"] = 0

# ---------------------------------------------------------------------------
# Authentication / JWT
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env_int("JWT_ACCESS_TOKEN_MINUTES", 60)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env_int("JWT_REFRESH_TOKEN_DAYS", 7)),
    "ROTATE_REFRESH_TOKENS": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",  # browsable API / admin
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticatedOrReadOnly",),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "config.pagination.DefaultPagination",
    "PAGE_SIZE": 12,
    # Throttling is a production NFR; the test suite intentionally hammers
    # endpoints, so rates are disabled when TESTING=1 (see conftest.py).
    "DEFAULT_THROTTLE_CLASSES": ()
    if env_bool("TESTING", False)
    else (
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ),
    "DEFAULT_THROTTLE_RATES": {
        "anon": "120/min",
        "user": "240/min",
        "auth": "20/min",  # register / token obtain
        "writes": "60/min",  # bookmarks / reviews / view tracking
        "chat": "20/min",  # live LLM fan-outs — the API-cost control valve
    },
}

if env_bool("TESTING", False):  # keep scoped throttles from failing test runs
    REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
        "anon": "100000/min",
        "user": "100000/min",
        "auth": "100000/min",
        "writes": "100000/min",
        "chat": "100000/min",
    }

# ---------------------------------------------------------------------------
# CORS — restricted to the frontend origin(s).
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
CORS_ALLOWED_ORIGIN_REGEXES = env_list("CORS_ALLOWED_ORIGIN_REGEXES")
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# Internationalisation / static
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Embeddings & search (see catalog/embeddings.py and catalog/search.py)
# ---------------------------------------------------------------------------
EMBEDDING_BACKEND = env_str("EMBEDDING_BACKEND", "auto")  # auto | sentence-transformers | hash
EMBEDDING_MODEL = env_str("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_DIM = env_int("EMBEDDING_DIM", 384)
# auto | celery | sync  (sync is for the test suite only)
EMBEDDING_DISPATCH = env_str("EMBEDDING_DISPATCH", "auto")

# Hybrid search: score = w * fts_rank_norm + (1 - w) * (1 - cosine_distance)
SEARCH_TEXT_WEIGHT = env_float("SEARCH_TEXT_WEIGHT", 0.4)

# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
RECOMMENDATION_DEFAULT_LIMIT = env_int("RECOMMENDATION_DEFAULT_LIMIT", 8)
# MMR diversity re-rank: lambda trades similarity vs. novelty (1.0 = pure similarity)
RECOMMENDATION_MMR_LAMBDA = env_float("RECOMMENDATION_MMR_LAMBDA", 0.7)
RECOMMENDATION_DIVERSIFY = env_bool("RECOMMENDATION_DIVERSIFY", True)

# ---------------------------------------------------------------------------
# Celery (optional — enabled with USE_CELERY=1)
# ---------------------------------------------------------------------------
USE_CELERY = env_bool("USE_CELERY", False)
CELERY_BROKER_URL = env_str("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env_str("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BEAT_SCHEDULE = {
    # Nightly refresh of every user's cached preference vector (Section 6.5.2)
    "recompute-all-preference-vectors": {
        "task": "recommendations.tasks.recompute_all_preference_vectors",
        "schedule": 3600.0,  # hourly; tune to taste (e.g. crontab for 3am)
    },
    # Link verification crawler: bounded batch every 6h -> each tool at most
    # once per 24h (see catalog/verification.py CHECK_INTERVAL).
    "verify-tool-links": {
        "task": "catalog.verify_tool_links",
        "schedule": 21600.0,
        "kwargs": {"limit": 50},
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{asctime} {levelname:7s} {name} | {message}", "style": "{"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "catalog": {"level": "INFO"},
        "recommendations": {"level": "INFO"},
    },
}
