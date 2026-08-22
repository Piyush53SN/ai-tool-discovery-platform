#!/usr/bin/env bash
# Codespaces post-create: prepare everything so `./scripts/run_local.sh`
# is the only command left to run. Torch-free by default (hash embedder);
# run `pip install -r requirements-dev.txt` manually for MiniLM.
set -e
cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
grep -v '^sentence-transformers' requirements.txt | pip install -r /dev/stdin
pip install pytest pytest-django ruff

# Pre-download embedded PostgreSQL and compile pgvector (needs git + gcc,
# both present in the devcontainer image) so the first run is instant.
python scripts/start_db.py --prepare-only

cd frontend && npm install
echo
echo "Ready. Next:  ./scripts/run_local.sh   ->  open the forwarded port 5173 (login: demo / demo-pass-123)"
