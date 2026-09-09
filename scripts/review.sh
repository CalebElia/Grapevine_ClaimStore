#!/usr/bin/env bash
# Start the mention-review UI, replacing any instance already on the port.
#
# WHY THIS SCRIPT EXISTS. The obvious one-liner is a trap:
#
#     pkill -f pipeline.review_server && python3 -m pipeline.review_server
#
# pkill -f matches the FULL COMMAND LINE of every process, and the shell running that very
# line contains the pattern -- so pkill kills its own parent shell, the && never runs, and
# nothing starts. The browser tab keeps showing the old page with no process behind it, which
# looks exactly like "my changes did not take effect".
#
# Killing by PORT cannot self-match: a shell holds no listening socket.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8765}"
PROPOSALS="${1:-}"

if pids=$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null) && [ -n "$pids" ]; then
    echo "[review] stopping what is on :$PORT (pid $(echo "$pids" | tr '\n' ' '))"
    kill $pids 2>/dev/null || true
    for _ in $(seq 20); do
        lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
        sleep 0.2
    done
    # Escalate only if it refused to go; a live socket would make the new server fail to bind.
    if lsof -ti "tcp:$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "[review] it did not stop; forcing"
        kill -9 $(lsof -ti "tcp:$PORT" -sTCP:LISTEN) 2>/dev/null || true
    fi
fi

args=(--port "$PORT")
[ -n "$PROPOSALS" ] && args+=(--proposals "$PROPOSALS")
echo "[review] starting on :$PORT — reload the browser tab once it says it is up"
exec python3 -m pipeline.review_server "${args[@]}"
