#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/.local-postgres"
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@15/bin}"
PORT="${RGX_PG_PORT:-55432}"
ACTION="${1:-}"

case "$ACTION" in
  start)
    if [[ ! -f "$DATA/PG_VERSION" ]]; then
      "$PG_BIN/initdb" -D "$DATA" -A trust --no-instructions >/dev/null
    fi
    "$PG_BIN/pg_ctl" -D "$DATA" -o "-h 127.0.0.1 -p $PORT" -l "$DATA/server.log" start
    echo "postgresql://$(whoami)@127.0.0.1:$PORT/postgres"
    ;;
  stop)
    "$PG_BIN/pg_ctl" -D "$DATA" stop
    ;;
  status)
    "$PG_BIN/pg_ctl" -D "$DATA" status
    ;;
  *)
    echo "Usage: scripts/local_postgres.sh start|stop|status" >&2
    exit 2
    ;;
esac
