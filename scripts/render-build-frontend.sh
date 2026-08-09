#!/bin/sh
set -eu

: "${AIFPL_API_URL:?Set AIFPL_API_URL to the public Render URL of aifpl-api}"
printf 'window.AIFPL_API_BASE = "%s";\n' "${AIFPL_API_URL%/}" > mockups/config.js
