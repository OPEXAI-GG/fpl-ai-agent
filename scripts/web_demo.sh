#!/usr/bin/env bash
# Start the read-only website demo: API, background workers and the Next.js site.
#
#   scripts/web_demo.sh            # install, prepare data if needed, start everything
#   DEMO_PUBLISH=1 scripts/web_demo.sh   # also publish a fresh data revision first
#
# What it does, in order (see docs/website.md):
#   1. installs locked dependencies (uv sync --frozen, npm ci)
#   2. if there is no football database, restores the latest export from the `data` release
#      (needs `gh` signed in) and imports it; nothing is written anywhere else
#   3. if no data revision has been published to the website yet (or DEMO_PUBLISH=1), runs
#      `fpl-agent web publish --ownership`: public FPL reads only, no notifications
#   4. starts the API, $WEB_WORKERS workers and the production Next.js build, then waits for
#      their health checks. Ctrl-C stops all of them.
#
# The website never signs in to FPL, never changes a team and never posts to Discord.
set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
WORKERS="${WEB_WORKERS:-2}"
export WEB_SOURCE_DB="${WEB_SOURCE_DB:-data_cache/football.duckdb}"
export WEB_DATA_DIR="${WEB_DATA_DIR:-data_cache/web}"
LOGS="${WEB_DATA_DIR}/logs"

need() { command -v "$1" >/dev/null 2>&1 || { echo "missing: $1 ($2)"; exit 2; }; }
need uv "https://docs.astral.sh/uv/"
need node "Node.js 20 or newer"
need npm "comes with Node.js"
node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 20 ? 0 : 1)' \
  || { echo "Node.js 20 or newer is required"; exit 2; }

echo "== dependencies"
uv sync --frozen
(cd web && npm ci --no-audit --no-fund --loglevel=error)

echo "== football data"
if [ ! -f "$WEB_SOURCE_DB" ]; then
  if command -v gh >/dev/null 2>&1; then
    scripts/data_restore.sh data_cache/export
  fi
  if [ -d data_cache/export ] && [ -n "$(ls -A data_cache/export 2>/dev/null)" ]; then
    uv run --frozen python -c "import sys; from fpl_agent.db.database import FootballDB; \
db = FootballDB(sys.argv[1]); db.import_parquet(sys.argv[2]); db.close()" \
      "$WEB_SOURCE_DB" data_cache/export
  else
    echo "No football database at $WEB_SOURCE_DB and no export to restore."
    echo "Restore one (scripts/data_restore.sh needs gh signed in to this repository) or"
    echo "build one with 'uv run fpl-agent db-update --points', then run this again."
    exit 2
  fi
fi
if [ "${DEMO_PUBLISH:-0}" = "1" ] || ! uv run --frozen fpl-agent web status | grep -q '^revision: r'; then
  uv run --frozen fpl-agent web publish --ownership
fi
uv run --frozen fpl-agent web status

echo "== starting"
mkdir -p "$LOGS"
pids=()
cleanup() {
  echo "stopping..."
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

uv run --frozen fpl-agent web serve --port "$API_PORT" >"$LOGS/api.log" 2>&1 &
pids+=($!)
for i in $(seq 1 "$WORKERS"); do
  uv run --frozen fpl-agent web worker >"$LOGS/worker$i.log" 2>&1 &
  pids+=($!)
done
(cd web && API_URL="http://127.0.0.1:$API_PORT" npm run build >"../$LOGS/web-build.log" 2>&1 \
  && API_URL="http://127.0.0.1:$API_PORT" exec npx next start -p "$WEB_PORT") >"$LOGS/web.log" 2>&1 &
pids+=($!)

wait_for() {  # url, name, seconds
  for _ in $(seq 1 "$3"); do
    if curl -sf "$1" >/dev/null 2>&1; then echo "ok: $2"; return 0; fi
    sleep 1
  done
  echo "FAILED: $2 did not answer at $1 (logs in $LOGS)"; exit 1
}
wait_for "http://127.0.0.1:$API_PORT/api/health" "API" 60
wait_for "http://127.0.0.1:$WEB_PORT/api/health" "website (and its API proxy)" 240

echo
echo "Website: http://localhost:$WEB_PORT   (sample team: http://localhost:$WEB_PORT/team/sample)"
echo "API health: http://localhost:$API_PORT/api/health   Logs: $LOGS"
echo "Ctrl-C to stop."
wait
