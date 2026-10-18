#!/usr/bin/env bash
# One command for the whole project:   ./run.sh          start all three services
#                                      ./run.sh test      run every test suite
#                                      ./run.sh ingest N  crawl the first N help-center pages (use 15 first)
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example. Add your ANTHROPIC_API_KEY, then re-run."; exit 1; }
set -a; . ./.env; set +a
KB_PORT=${KB_PORT:-8001}; INBOX_PORT=${INBOX_PORT:-3002}; SCOPE_PORT=${SCOPE_PORT:-8003}
export KB_URL="http://localhost:${KB_PORT}"

setup_py() {  # $1 = task dir
  ( cd "$1"
    [ -d .venv ] || python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt )
}
setup_ts() { ( cd task2-inbox-agent; [ -d node_modules ] || npm install --no-audit --no-fund --silent ); }

case "${1:-run}" in
  test)
    setup_py task1-knowledge-brain; setup_py task3-meeting-to-scope; setup_ts
    ( cd task1-knowledge-brain && KB_EMBEDDER=hash .venv/bin/python -m pytest -q )
    ( cd task2-inbox-agent && npx tsc --noEmit && npx vitest run )
    ( cd task3-meeting-to-scope && .venv/bin/python -m pytest -q ) ;;
  ingest)
    curl -fsS -X POST "$KB_URL/ingestion/run" -H "X-API-Key: ${KB_API_KEY:-}" -H 'Content-Type: application/json' -d "{\"limit\": ${2:-15}}"
    echo; echo "Watch progress: curl -H 'X-API-Key: \$KB_API_KEY' $KB_URL/ingestion/status" ;;
  run)
    [ -n "${ANTHROPIC_API_KEY:-}" ] || echo "WARNING: ANTHROPIC_API_KEY is empty. Tasks 1 and 3 need it; the Inbox Agent falls back to its offline mock model."
    setup_py task1-knowledge-brain; setup_py task3-meeting-to-scope; setup_ts
    mkdir -p logs
    trap 'kill 0' EXIT INT TERM
    ( cd task1-knowledge-brain && .venv/bin/uvicorn app.main:app_factory --factory --port "$KB_PORT" > ../logs/knowledge-brain.log 2>&1 ) &
    for i in $(seq 1 60); do curl -fsS "$KB_URL/health" >/dev/null 2>&1 && break; sleep 1; done
    ( cd task2-inbox-agent && PORT="$INBOX_PORT" npx tsx src/server.ts > ../logs/inbox-agent.log 2>&1 ) &
    ( cd task3-meeting-to-scope && .venv/bin/uvicorn app.main:app_factory --factory --port "$SCOPE_PORT" > ../logs/meeting-to-scope.log 2>&1 ) &
    echo "Knowledge Brain  http://localhost:$KB_PORT   (chat UI + /docs)"
    echo "Inbox Agent      http://localhost:$INBOX_PORT"
    echo "Meeting to Scope http://localhost:$SCOPE_PORT"
    echo "First time? In another terminal run: ./run.sh ingest 15"
    wait ;;
  *) echo "usage: ./run.sh [run|test|ingest N]"; exit 2 ;;
esac
