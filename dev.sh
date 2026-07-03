#!/usr/bin/env bash
# Dev-Stack: DB (Container) + Backend (uvicorn --reload) + Frontend (vite).
#   ./dev.sh [start] | stop | reset
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RUN="$ROOT/.dev"                 # pidfiles + logs (gitignored)
DB_CONTAINER="weltmodell-db"
DB_VOLUME="weltmodell-pgdata"
DB_IMAGE="weltmodell-db:latest"
API_PORT=8100
FE_PORT=5174

CE="${CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"
[ -n "$CE" ] || { echo "need podman or docker on PATH" >&2; exit 1; }
SETSID="$(command -v setsid || true)"   # new process group → clean group-kill
mkdir -p "$RUN"

# --- generic process supervision ------------------------------------------
start_service() {  # name cmd...
  local name=$1; shift
  local pidf="$RUN/$name.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then
    echo "$name already running (pid $(cat "$pidf"))"; return
  fi
  $SETSID "$@" >"$RUN/$name.log" 2>&1 &
  echo $! >"$pidf"
  echo "$name up (pid $!)"
}

stop_service() {  # name
  local pidf="$RUN/$1.pid" pid
  [ -f "$pidf" ] || return 0
  pid=$(cat "$pidf")
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    echo "$1 stopped"
  fi
  rm -f "$pidf"
}

# --- database container -----------------------------------------------------
db_running() { [ "$("$CE" inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null)" = "true" ]; }
db_exists()  { "$CE" container inspect "$DB_CONTAINER" >/dev/null 2>&1; }

db_wait() {  # first boot needs a few seconds before it accepts connections
  local i
  for i in $(seq 1 30); do
    "$CE" exec "$DB_CONTAINER" pg_isready -U weltmodell -d weltmodell >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "db not ready after 30s" >&2; return 1
}

db_up() {
  if db_running; then echo "db already running"; return; fi
  if db_exists; then
    "$CE" start "$DB_CONTAINER" >/dev/null; echo "db started"
  else
    "$CE" image inspect "$DB_IMAGE" >/dev/null 2>&1 || \
      { echo "building db image…"; "$CE" build -t "$DB_IMAGE" -f "$ROOT/db/Containerfile" "$ROOT/db"; }
    "$CE" run -d --name "$DB_CONTAINER" \
      -e POSTGRES_USER=weltmodell -e POSTGRES_PASSWORD=weltmodell -e POSTGRES_DB=weltmodell \
      -p 5433:5432 -v "$DB_VOLUME":/var/lib/postgresql/data "$DB_IMAGE" >/dev/null
    echo "db created"
  fi
  db_wait
}

# --- commands ---------------------------------------------------------------
cmd_start() {
  db_up
  echo "syncing backend deps…"; uv sync --quiet
  [ -d "$ROOT/frontend/node_modules" ] || { echo "installing frontend deps…"; npm --prefix "$ROOT/frontend" install; }
  # migrations run automatically on api startup (src/weltmodell/api.py)
  start_service backend  uv run uvicorn weltmodell.api:app --port "$API_PORT" --reload --reload-dir src
  start_service frontend npm --prefix "$ROOT/frontend" run dev
  cat <<EOF

  stack up (hot reload)
    API   http://localhost:$API_PORT       (docs: /docs)
    UI    http://localhost:$FE_PORT        (proxy → API)
    logs  tail -f $RUN/{backend,frontend}.log
    stop  $0 stop
EOF
}

cmd_stop() {
  stop_service frontend
  stop_service backend
  if db_running; then "$CE" stop "$DB_CONTAINER" >/dev/null && echo "db stopped"; fi
}

cmd_reset() {
  cmd_stop
  "$CE" rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
  "$CE" volume rm "$DB_VOLUME" >/dev/null 2>&1 || true
  echo "db wiped — fresh volume, migrations reapply on start"
  cmd_start
}

case "${1:-start}" in
  start) cmd_start ;;
  stop)  cmd_stop ;;
  reset) cmd_reset ;;
  *) echo "usage: $0 {start|stop|reset}" >&2; exit 1 ;;
esac
