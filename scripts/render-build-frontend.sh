#!/bin/sh
set -eu

api_url="${AIFPL_API_URL:-https://aifpl-api.onrender.com}"
printf 'window.AIFPL_API_BASE = "%s";\n' "${api_url%/}" > mockups/config.js
