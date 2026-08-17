#!/bin/sh
set -eu

active_pid=""

stop_active_command() {
  if [ -n "$active_pid" ] && kill -0 "$active_pid" 2>/dev/null; then
    kill -TERM "$active_pid" 2>/dev/null || true
    wait "$active_pid" 2>/dev/null || true
  fi
  exit 143
}

run_command() {
  "$@" &
  active_pid=$!
  set +e
  wait "$active_pid"
  command_status=$?
  set -e
  active_pid=""
  return "$command_status"
}

trap stop_active_command HUP INT TERM

data_dir="${AIFPL_DATA_DIR:-data}"
decision_dir="$data_dir/hermes/decisions"
marker="$data_dir/.deployed-commit"

case "${AIFPL_RENDER_BOOTSTRAP:-true}" in
  true|1|yes) ;;
  *)
    echo "AIFPL_RENDER_BOOTSTRAP is disabled; skipping Render bootstrap."
    exit 0
    ;;
esac

has_decision=false
if [ -d "$decision_dir" ]; then
  for decision in "$decision_dir"/*.json; do
    if [ -f "$decision" ]; then
      has_decision=true
      break
    fi
  done
fi

season="${AIFPL_RENDER_BOOTSTRAP_SEASON:-2025-26}"
historical_dir="$data_dir/normalized/historical/$season/imports"
import_history() {
  if [ -d "$historical_dir" ] && ls "$historical_dir"/*.json > /dev/null 2>&1; then
    echo "Historical season $season already imported; skipping import."
  else
    echo "Importing historical season $season for transfer awareness..."
    run_command aifpl import-season "$season"
  fi
}

current_commit="$(git rev-parse --short HEAD 2>/dev/null || true)"
if [ -n "$current_commit" ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$current_commit" ] && [ "$has_decision" = "true" ]; then
  echo "AIFPL data is current for commit $current_commit; skipping bootstrap."
  exit 0
fi

import_history

if [ "$has_decision" = "true" ]; then
  echo "Code or data changed (commit ${current_commit:-unknown}); re-running refresh and Hermes..."
else
  echo "Initializing AIFPL data on the persistent Render disk..."
fi

run_command aifpl refresh-current-data \
  --start-gameweek "${AIFPL_RENDER_BOOTSTRAP_START_GAMEWEEK:-1}" \
  --end-gameweek "${AIFPL_RENDER_BOOTSTRAP_END_GAMEWEEK:-6}" \
  --budget "${AIFPL_RENDER_BOOTSTRAP_BUDGET:-1000}"
# Reinitialization is backend-gated: it rebuilds the pre-season opening squad
# automatically whenever the committed plan's planner_version is stale, and is
# a no-op afterwards and after GW1.
if [ "$has_decision" = "true" ]; then
  if run_command aifpl hermes-reinitialize-opening-squad; then
    echo "Reinitialized the pre-season opening squad with the horizon optimizer."
  else
    reinitialize_status=$?
    if [ "$reinitialize_status" -eq 2 ]; then
      run_command aifpl hermes-run
    else
      exit "$reinitialize_status"
    fi
  fi
else
  run_command aifpl hermes-run
fi
if [ -n "$current_commit" ]; then
  mkdir -p "$data_dir"
  printf '%s\n' "$current_commit" > "$marker"
fi
echo "Bootstrap complete."
