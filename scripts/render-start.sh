#!/bin/sh
set -eu

sh scripts/render-bootstrap.sh > /tmp/aifpl-bootstrap.log 2>&1 &

exec uvicorn aifpl.api:app --host 0.0.0.0 --port "${PORT:-8000}"
