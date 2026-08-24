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
bold "• Preparing frontend deps (npm install — first run can be slow)"
# Always run npm install: it is a fast no-op when current, and SKIPPING it on
# an old node_modules broke upgrades before (missing @fontsource deps made
# Vite fail to resolve the stylesheet's font imports).
( cd frontend && npm install --silent )

# Pre-flight: a leftover server from a previous run (e.g. a terminal that was
# closed instead of Ctrl-C'd) would silently steal the documented ports.
# strictPort now makes Vite fail loudly; catch it here with actionable detail.
check_port_free() {
  local port="$1"
  local holder
  # `|| true`: with pipefail, an EMPTY result (port free) makes grep exit 1
  # and would otherwise kill the script — the happy path must survive.
  holder="$(ss -tlnp 2>/dev/null | grep ":${port} " | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2 || true)"
  if [[ -n "$holder" ]]; then
    bold "✗ Port ${port} is already taken by PID ${holder}:"
    ps -fp "${holder}" || true
    echo "  This is a leftover server from an earlier run. Reclaim the port with:"
    echo "      kill ${holder}     # or: pkill -f 'vite' ; pkill -f 'runserver'"
    echo "  then re-run ./scripts/run_local.sh"
    exit 1
  fi
}
check_port_free 5173
check_port_free 8000

bold "• Launching Django (8000) + Vite (5173) — waiting for BOTH to answer…"

# Log files: output still streams to the terminal via tee, and a failed boot
# can be tailed below instead of leaving a silent hang.
RUN_LOG_DIR=".run"
mkdir -p "$RUN_LOG_DIR"

# HUP matters: closing the terminal window sends SIGHUP (not INT/TERM) —
# without it the trap never fires and the background servers get orphaned,
# which is exactly how a stale Vite ends up holding 5173.
trap 'kill 0' EXIT INT TERM HUP
( cd frontend && npm run dev ) 2>&1 | tee "$RUN_LOG_DIR/vite.log" &
python manage.py runserver 0.0.0.0:8000 2>&1 | tee "$RUN_LOG_DIR/django.log" &

# ---- readiness gate: prove BOTH servers respond before declaring victory ----
# `curl ... 2>/dev/null` failures inside `if` are non-fatal under `set -e`;
# each poll is guarded so a refused connection just means "try again in 1s".
wait_for() {
  local url="$1" name="$2" log="$3" timeout_s="${4:-45}" waited=0
  while true; do
    if curl -fsS --max-time 2 -o /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    waited=$((waited + 1))
    if (( waited >= timeout_s )); then
      bold "✗ ${name} did not answer at ${url} within ${timeout_s}s — last log lines:"
      tail -n 20 "$log" || true
      return 1
    fi
    sleep 1
  done
}

if wait_for "http://localhost:8000/" "Django API" "$RUN_LOG_DIR/django.log" 45    && wait_for "http://localhost:5173/" "Vite frontend" "$RUN_LOG_DIR/vite.log" 45; then
  echo
  bold "────────────────────────────────────────────────────────────"
  bold "  READY — both servers verified responding"
  bold "  App:      http://localhost:5173"
  bold "  API:      http://localhost:8000/api/"
  bold "  Login:    demo / demo-pass-123"
  bold "  Stop:     Ctrl-C (stops both)"
  bold "────────────────────────────────────────────────────────────"
else
  bold "✗ Startup failed — see the log tail above. Nothing is marked ready."
  exit 1
fi

wait
