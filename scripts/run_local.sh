#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-command local run:  ./scripts/run_local.sh
#
#   1. starts PostgreSQL+pgvector via docker compose (skipped if DATABASE_URL
#      already points at a reachable server)
#   2. creates .venv + installs dependencies (lightweight by default: the
#      deterministic hash embedder, no torch — pass --with-model for MiniLM)
#   3. writes .env if missing, migrates, seeds ~160 tools + demo user
#   4. launches Django (8000) and Vite (5173); Ctrl-C stops both
#
# Then open http://localhost:5173  (login: demo / demo-pass-123)
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_MODEL=0
[[ "${1:-}" == "--with-model" ]] && WITH_MODEL=1

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

# ---- 1. database ----------------------------------------------------------
if [[ -f .env ]]; then
  bold "• Using existing .env"
else
  export DATABASE_URL="${DATABASE_URL:-postgresql://postgres:postgres@localhost:5432/aitools}"
  if command -v docker >/dev/null 2>&1; then
    bold "• Starting PostgreSQL + pgvector (docker compose)"
    docker compose up -d --wait
  else
    echo "docker not found and no .env present — set DATABASE_URL to an existing" \
         "PostgreSQL with pgvector and re-run." >&2
    exit 1
  fi
  cat > .env <<EOF
SECRET_KEY=local-dev-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=$DATABASE_URL
EMBEDDING_BACKEND=$([[ $WITH_MODEL -eq 1 ]] && echo auto || echo hash)
EMBEDDING_DISPATCH=auto
SEARCH_TEXT_WEIGHT=0.4
EOF
  bold "• Wrote .env (EMBEDDING_BACKEND=$([[ $WITH_MODEL -eq 1 ]] && echo auto || echo hash))"
fi

# ---- 2. python env ----------------------------------------------------------
if [[ ! -d .venv ]]; then
  bold "• Creating virtualenv"
  python3 -m venv .venv
fi
source .venv/bin/activate

bold "• Installing dependencies (this is the slowest step, once)"
if [[ $WITH_MODEL -eq 1 ]]; then
  pip install -q -r requirements-dev.txt
else
  grep -v '^sentence-transformers' requirements.txt | pip install -q -r /dev/stdin
  pip install -q pytest pytest-django ruff 2>/dev/null || true
fi

# ---- 3. migrate + seed -------------------------------------------------------
bold "• Applying migrations"
python manage.py migrate

if python manage.py shell -c "from catalog.models import Tool; import sys; sys.exit(0 if Tool.objects.count() > 0 else 1)" 2>/dev/null; then
  bold "• Catalog already seeded ($(python manage.py shell -c 'from catalog.models import Tool; print(Tool.objects.count())' 2>/dev/null | tail -1) tools)"
else
  bold "• Seeding catalog (~160 tools + demo user) — first run takes a minute"
  python manage.py seed_tools
fi

# ---- 4. run both servers ------------------------------------------------------
bold "• Starting Django (8000) + Vite (5173) — Ctrl-C stops everything"
( cd frontend && [[ -d node_modules ]] || npm install --silent )
trap 'kill 0' EXIT INT TERM
( cd frontend && npm run dev ) &
python manage.py runserver 0.0.0.0:8000 &
wait
