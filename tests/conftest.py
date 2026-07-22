"""Shared test fixtures for the Yunkan integration."""

from __future__ import annotations

from pathlib import Path
import sys

# Make ``custom_components.yunkan`` importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
