#!/bin/sh
set -eu

bootstrap_pid=""
scheduler_pid=""
server_pid=""

stop_process() {
  pid="$1"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
  fi
}

wait_for_process() {
  pid="$1"
  if [ -n "$pid" ]; then
    wait "$pid" 2>/dev/null || true
  fi
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  stop_process "$server_pid"
  stop_process "$scheduler_pid"
  stop_process "$bootstrap_pid"
  wait_for_process "$server_pid"
  wait_for_process "$scheduler_pid"
  wait_for_process "$bootstrap_pid"
  exit "$status"
}

trap cleanup EXIT HUP INT TERM

sh scripts/render-bootstrap.sh > /tmp/aifpl-bootstrap.log 2>&1 &
bootstrap_pid=$!
aifpl run-deadline-scheduler > /tmp/aifpl-scheduler.log 2>&1 &
scheduler_pid=$!
uvicorn aifpl.api:app --host 0.0.0.0 --port "${PORT:-8000}" &
server_pid=$!

set +e
wait "$server_pid"
server_status=$?
set -e
exit "$server_status"
