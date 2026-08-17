#!/bin/sh
set -eu

exec uvicorn aifpl.api:app --host 0.0.0.0 --port "${PORT:-8000}"
