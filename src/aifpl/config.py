from __future__ import annotations

import os
from pathlib import Path


FPL_BASE_URL = "https://fantasy.premierleague.com/api"


def data_dir() -> Path:
    """Return the configurable local location for downloaded source data."""
    return Path(os.environ.get("AIFPL_DATA_DIR", "data")).expanduser()
