"""Pytest config: make backend/ importable the same way the app runs (from backend/)."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
