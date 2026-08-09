from __future__ import annotations

import os
import re
import secrets


def redact_secrets(message: str) -> str:
    redacted = re.sub(r"(?i)(apiKey|api_key|token|password|secret)=([^&\s]+)", r"\1=[REDACTED]", message)
    for name, value in os.environ.items():
        if value and any(marker in name.upper() for marker in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def valid_admin_key(supplied: str | None) -> bool:
    expected = os.environ.get("AIFPL_ADMIN_API_KEY")
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))
