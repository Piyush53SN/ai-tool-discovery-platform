#!/usr/bin/env bash
# Codespaces post-create: prepare everything so `./scripts/run_local.sh`
# is the only command left to run. Uses the torch-free install by default;
# re-run `pip install -r requirements-dev.txt` manually for MiniLM.
set -e
cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
grep -v '^sentence-transformers' requirements.txt | pip install -r /dev/stdin
pip install pytest pytest-django ruff

cd frontend && npm install
echo
echo "Ready. Next:  ./scripts/run_local.sh   ->  http://localhost:5173 (login: demo / demo-pass-123)"
