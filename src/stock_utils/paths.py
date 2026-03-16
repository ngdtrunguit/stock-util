"""Stable filesystem paths anchored to the repository root."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
OUTPUT_DIR = DATA_DIR / "output"
SECTORS_DIR = DATA_DIR / "sectors"
SECTORS_FILE = DATA_DIR / "sectors.json"
DEFAULT_WATCHLIST_FILE = DATA_DIR / "watchlist.txt"


def resolve_repo_path(path_value: str | Path) -> Path:
    """Resolve a path relative to the repository root when not absolute."""
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path
