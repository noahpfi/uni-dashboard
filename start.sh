#!/usr/bin/env bash

ROOT="$(cd "$(dirname "$0")" && pwd)"

cleanup() {
  echo ""
  echo "Stopping..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
}
trap cleanup INT TERM

echo "Killing existing processes..."
lsof -ti:8000 | xargs kill -9 2>/dev/null
lsof -ti:5173 | xargs kill -9 2>/dev/null
sleep 1

echo "Starting backend..."
python3 "$ROOT/scraper/api.py" &
BACKEND_PID=$!

echo "Starting frontend..."
cd "$ROOT/frontend" && npm run dev -- --open --host &
FRONTEND_PID=$!

echo "Press Ctrl+C to stop."

wait
