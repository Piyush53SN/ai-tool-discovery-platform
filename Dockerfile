# Django API image for Render (see DEPLOY.md).
#
# Default build: lightweight — no torch, embeddings use the deterministic
# offline `hash` backend (EMBEDDING_BACKEND=auto falls back to it when
# sentence-transformers is not importable). Small image, fast builds, fits
# Render's Starter plan.
#
# Optional: build with the real MiniLM embedding model (~1.5 GB bigger
# image, needs >= 1 vCPU / 2 GB on Render):
#   docker build --build-arg INSTALL_MLM=1 -t aitools-api .
# In Render: Service settings → "Docker build args" → INSTALL_MLM=1
#
# Startup: migrations run synchronously (fast); seeding runs in the
# background so the health check passes quickly, then the 127-tool catalog
# appears within ~1 minute. seed_tools is idempotent (matched by slug).

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first to keep image layers cached.
# CPU-only torch (from the official wheel index) instead of the default
# CUDA wheels from PyPI, when requested.
COPY requirements.txt .
ARG INSTALL_MLM=0
RUN if [ "$INSTALL_MLM" = "1" ]; then \
        pip install torch --index-url https://download.pytorch.org/whl/cpu && \
        pip install -r requirements.txt gunicorn; \
    else \
        grep -v '^sentence-transformers' requirements.txt > /tmp/reqs.txt && \
        pip install -r /tmp/reqs.txt gunicorn; \
    fi

COPY . .

EXPOSE 8000

# Render sets $PORT. `exec` so gunicorn is PID 1 and receives SIGTERM.
# GUNICORN_WORKERS: keep at 1 on the free/Hobby instance (512 MB RAM);
# raise to 2-4 on paid plans with more memory.
CMD ["sh", "-c", "python manage.py migrate --noinput && (python manage.py seed_tools >/seed.log 2>&1 & ) && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${GUNICORN_WORKERS:-1} --timeout 120"]
