#!/usr/bin/env bash
# RippleBack dev launcher (macOS / Linux).
#
# Starts all three processes: the MCP server, the API adapter, and the Vite dev
# UI. Ctrl+C stops everything it started.
#
#   ./scripts/dev.sh
#
# Requires: a .venv with the backend installed, and Node.js for the frontend.

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/bin/python"

if [[ ! -x "$python" ]]; then
    echo "No .venv found. Run: python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

frontend="$root/frontend"
if [[ ! -d "$frontend/node_modules" ]]; then
    echo "Installing frontend dependencies (npm ci)..."
    (cd "$frontend" && npm ci)
fi

adapter_port="${RIPPLEBACK_ADAPTER_PORT:-8001}"
pids=()

cleanup() {
    echo
    echo "Stopping..."
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "Starting MCP server (http://127.0.0.1:8000/mcp)..."
"$python" -m rippleback.server &
pids+=($!)

echo "Starting API adapter (http://127.0.0.1:${adapter_port})..."
"$python" -m rippleback.adapter &
pids+=($!)

echo "Starting Vite UI (http://localhost:5173)..."
(cd "$frontend" && npm run dev) &
pids+=($!)

echo
echo "  Dashboard : http://localhost:5173"
echo "  Adapter   : http://127.0.0.1:${adapter_port}/api/health"
echo "  MCP server: http://127.0.0.1:8000/mcp"
echo
echo "Press Ctrl+C to stop."

wait
