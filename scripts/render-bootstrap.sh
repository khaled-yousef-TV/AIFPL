#!/bin/sh
set -eu

data_dir="${AIFPL_DATA_DIR:-data}"
decision_dir="$data_dir/hermes/decisions"
projection_dir="$data_dir/normalized/current/odds_projections"

case "${AIFPL_RENDER_BOOTSTRAP:-true}" in
  true|1|yes) ;;
  *)
    echo "AIFPL_RENDER_BOOTSTRAP is disabled; skipping Render bootstrap."
    exit 0
    ;;
esac

latest_decision=""
if [ -d "$decision_dir" ]; then
  for decision in "$decision_dir"/*.json; do
    [ -f "$decision" ] || continue
    name="$(basename "$decision")"
    if [ -z "$latest_decision" ] || [ "$name" \> "$latest_decision" ]; then
      latest_decision="$name"
    fi
  done
fi

latest_projection=""
if [ -d "$projection_dir" ]; then
  for projection in "$projection_dir"/*.jsonl; do
    [ -f "$projection" ] || continue
    name="$(basename "$projection")"
    stamp="$(printf '%s' "$name" | sed -E 's/^gw[0-9]+-[0-9]+\.([^.]+)\..*/\1/')"
    if [ -z "$latest_projection" ] || [ "$stamp" \> "$latest_projection" ]; then
      latest_projection="$stamp"
    fi
  done
fi

if [ -n "$latest_decision" ] && { [ -z "$latest_projection" ] || [ "$latest_decision" \> "$latest_projection" ]; }; then
  echo "AIFPL data is current (decision $latest_decision newer than projections); skipping bootstrap."
  exit 0
fi

if [ -n "$latest_decision" ]; then
  echo "Stale projections ($latest_projection) newer than decision ($latest_decision); re-running refresh and Hermes..."
else
  echo "Initializing AIFPL data on the persistent Render disk..."
fi

aifpl refresh-current-data \
  --start-gameweek "${AIFPL_RENDER_BOOTSTRAP_START_GAMEWEEK:-1}" \
  --end-gameweek "${AIFPL_RENDER_BOOTSTRAP_END_GAMEWEEK:-6}" \
  --budget "${AIFPL_RENDER_BOOTSTRAP_BUDGET:-1000}"
aifpl hermes-run
echo "Bootstrap complete."
