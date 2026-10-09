#!/usr/bin/env bash
# Start the Notice -> Action backend on http://127.0.0.1:8000
# Override with NOTICE_PORT=9000 ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

# Prefer NOTICE_PORT; fall back to PORT only when it is a real, non-zero port
# (some shells export PORT=0, which would hand uvicorn an ephemeral port).
PORT="${NOTICE_PORT:-${PORT:-8000}}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]] || [ "$PORT" -eq 0 ]; then
  PORT=8000
fi

if ! curl -s -o /dev/null http://127.0.0.1:11434/api/tags; then
  echo "Ollama is not running - starting 'ollama serve' in the background..."
  nohup ollama serve > /tmp/ollama.log 2>&1 &
  for _ in $(seq 1 20); do
    curl -s -o /dev/null http://127.0.0.1:11434/api/tags && break
    sleep 1
  done
fi

echo "Serving on http://127.0.0.1:${PORT}  (model check: see /api/health)"
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}"
