#!/usr/bin/env bash
# Build what is missing, then serve the terminal on :8000.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x backend/.venv/bin/python ]; then
  python3 -m venv backend/.venv
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi

if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm install --no-audit --no-fund && npm run build)
fi

exec backend/.venv/bin/uvicorn app.main:app \
  --app-dir backend \
  --host 0.0.0.0 \
  --port "${PORT:-8000}"
