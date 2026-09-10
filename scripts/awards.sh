#!/usr/bin/env bash
# Rule on award_status. See pipeline/award_status.py for what each group means.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8766}"
if pids=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null) && [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
    for _ in $(seq 20); do lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break; sleep 0.2; done
fi
exec python3 -m pipeline.award_status_server --port "$PORT" "$@"
