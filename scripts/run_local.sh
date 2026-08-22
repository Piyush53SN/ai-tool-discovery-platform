#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-command local run:  ./scripts/run_local.sh
#
#   1. creates .venv + installs dependencies (lightweight by default: the
#      deterministic hash embedder, no torch — pass --with-model for MiniLM)
#   2. provides PostgreSQL+pgvector, in this order:
#        a. existing .env (uses your DATABASE_URL as-is)
#        b. Docker (docker compose up)          — nicest on laptops
#        c. embedded PostgreSQL via pip         — no Docker needed at all
#           (works great in GitHub Codespaces)
#   3. migrates, seeds ~160 tools + demo user
#   4. launches Django (8000) and Vite (5173); Ctrl-C stops both
#
# Then open http://localhost:5173  (login: demo / demo-pass-123)
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")/.."

WITH_MODEL=0
[[ "${1:-}" == "--with-model" ]] && WITH_MODEL=1

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

# ---- 1. python env ----------------------------------------------------------
if [[ ! -d .venv ]]; then
  bold "• Creating virtualenv"
  python3 -m venv .venv
fi
source .venv/bin/activate

bold "• Installing dependencies (slowest step, happens once)"
if [[ $WITH_MODEL -eq 1 ]]; then
  pip install -q -r requirements-dev.txt
else
  grep -v '^sentence-transformers' requirements.txt | pip install -q -r /dev/stdin
  pip install -q pytest pytest-django ruff 2>/dev/null || true
fi

# ---- 2. database -------------------------------------------------------------
if [[ -f .env ]]; then
  bold "• Using existing .env"
else
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    bold "• Starting PostgreSQL + pgvector via Docker"
    docker compose up -d --wait
    DB_URL="postgresql://postgres:postgres@localhost:5432/aitools"
  elif command -v python3 >/dev/null 2>&1 && uname -s | grep -qi linux; then
    bold "• No usable Docker — starting embedded PostgreSQL (pip pgserver + pgvector)"
    DB_URL="$(python scripts/start_db.py --workdir .pgdata --db aitools | tail -n 1)"
  else
    echo "No .env, no Docker, and embedded Postgres needs Linux." \
         "Point DATABASE_URL at a PostgreSQL with pgvector and re-run." >&2
    exit 1
  fi

  cat > .env <<EOF
SECRET_KEY=local-dev-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=$DB_URL
EMBEDDING_BACKEND=$([[ $WITH_MODEL -eq 1 ]] && echo auto || echo hash)
EMBEDDING_DISPATCH=auto
SEARCH_TEXT_WEIGHT=0.4
EOF
  bold "• Wrote .env"
fi

# ---- 3. migrate + seed -------------------------------------------------------
bold "• Applying migrations"
python manage.py migrate

if python manage.py shell -c "from catalog.models import Tool; import sys; sys.exit(0 if Tool.objects.count() > 0 else 1)" 2>/dev/null; then
  bold "• Catalog already seeded"
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
