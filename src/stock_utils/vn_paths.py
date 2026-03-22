"""VN-specific filesystem paths.

This module keeps VN artifacts separated from the US pipeline artifacts.
"""

from __future__ import annotations

from stock_utils.paths import DATA_DIR


VN_SECTORS_DIR = DATA_DIR / "sectors-vn"
VN_SECTORS_FILE = DATA_DIR / "sectors-vn.json"
