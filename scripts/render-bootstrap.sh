#!/bin/sh
set -eu

data_dir="${AIFPL_DATA_DIR:-data}"
decision_dir="$data_dir/hermes/decisions"
has_decision=false

if [ -d "$decision_dir" ]; then
  for decision in "$decision_dir"/*.json; do
    if [ -f "$decision" ]; then
      has_decision=true
      break
    fi
  done
fi

if [ "$has_decision" = "true" ]; then
  echo "AIFPL data already initialized; skipping Render bootstrap."
  exit 0
fi

case "${AIFPL_RENDER_BOOTSTRAP:-true}" in
  true|1|yes) ;;
  *)
    echo "AIFPL_RENDER_BOOTSTRAP is disabled and no Hermes decision exists."
    exit 0
    ;;
esac

echo "Initializing AIFPL data on the persistent Render disk..."
aifpl refresh-current-data \
  --start-gameweek "${AIFPL_RENDER_BOOTSTRAP_START_GAMEWEEK:-1}" \
  --end-gameweek "${AIFPL_RENDER_BOOTSTRAP_END_GAMEWEEK:-6}" \
  --budget "${AIFPL_RENDER_BOOTSTRAP_BUDGET:-1000}"
aifpl hermes-run
