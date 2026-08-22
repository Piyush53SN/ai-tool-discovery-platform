@echo off
REM One-command local run on Windows (needs Docker Desktop, Python 3.11+, Node 18+):
REM   scripts\run_local.bat
REM Then open http://localhost:5173  (login: demo / demo-pass-123)
setlocal
cd /d "%~dp0.."

if not exist .env (
  echo . Starting PostgreSQL + pgvector ^(docker compose^)
  docker compose up -d --wait
  (
    echo SECRET_KEY=local-dev-change-me
    echo DEBUG=True
    echo ALLOWED_HOSTS=localhost,127.0.0.1
    echo DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aitools
    echo EMBEDDING_BACKEND=hash
    echo EMBEDDING_DISPATCH=auto
    echo SEARCH_TEXT_WEIGHT=0.4
  ) > .env
  echo . Wrote .env ^(lightweight hash embedder; see README for MiniLM^)
)

if not exist .venv (
  echo . Creating virtualenv
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo . Installing dependencies ^(once^)
findstr /v /b /c:"sentence-transformers" requirements.txt > "%TEMP%\reqs.txt"
pip install -q -r "%TEMP%\reqs.txt" pytest pytest-django

echo . Migrating
python manage.py migrate

python manage.py shell -c "import sys; from catalog.models import Tool; sys.exit(0 if Tool.objects.count() else 1"
if errorlevel 1 (
  echo . Seeding catalog ~160 tools + demo user - first run takes a minute
  python manage.py seed_tools
)

echo . Starting Django ^(8000^) and Vite ^(5173^) in separate windows
start "Django API" cmd /k python manage.py runserver 0.0.0.0:8000
pushd frontend
if not exist node_modules call npm install --silent
start "Vite frontend" cmd /k npm run dev
popd
echo.
echo Open http://localhost:5173  ^(login: demo / demo-pass-123^)
endlocal
