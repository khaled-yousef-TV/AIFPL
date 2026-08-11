#!/bin/sh
set -eu

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

current_commit="$(git rev-parse --short HEAD 2>/dev/null || true)"
if [ -n "$current_commit" ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$current_commit" ] && [ "$has_decision" = "true" ]; then
  echo "AIFPL data is current for commit $current_commit; skipping bootstrap."
  exit 0
fi

if [ "$has_decision" = "true" ]; then
  echo "Code or data changed (commit ${current_commit:-unknown}); re-running refresh and Hermes..."
else
  echo "Initializing AIFPL data on the persistent Render disk..."
fi

aifpl refresh-current-data \
  --start-gameweek "${AIFPL_RENDER_BOOTSTRAP_START_GAMEWEEK:-1}" \
  --end-gameweek "${AIFPL_RENDER_BOOTSTRAP_END_GAMEWEEK:-6}" \
  --budget "${AIFPL_RENDER_BOOTSTRAP_BUDGET:-1000}"
aifpl hermes-run
if [ -n "$current_commit" ]; then
  mkdir -p "$data_dir"
  printf '%s\n' "$current_commit" > "$marker"
fi
echo "Bootstrap complete."
