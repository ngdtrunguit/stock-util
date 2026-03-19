"""Lightweight SQLite persistence layer for daily screening results.

Three tables (one per screening strategy) accumulate candidates across runs so
that performance can be tracked and analysed over time.

Usage::

    from stock_utils.database import ScreeningDatabase

    db = ScreeningDatabase()          # uses default DB_PATH
    db.save_candidates(candidates, strategy="rsi_oversold_bounce_ma200",
                       run_date="2024-01-15", run_timestamp="2024-01-15T00:05:00+00:00")
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from stock_utils.paths import DB_PATH

LOGGER = logging.getLogger(__name__)

# Mapping from strategy name to table name
_STRATEGY_TABLE: dict[str, str] = {
    "rsi_oversold_bounce_ma200": "rsi_oversold_candidates",
    "golden_cross_weekly": "golden_cross_candidates",
    "price_above_ma50_ma200": "ma_above_candidates",
}

# Explicit set of valid table names derived from the mapping above.
# Used to guard SQL statements that cannot use parameterised identifiers.
_VALID_TABLES: frozenset[str] = frozenset(_STRATEGY_TABLE.values())

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS {table} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT    NOT NULL,
    run_timestamp   TEXT    NOT NULL,
    symbol          TEXT    NOT NULL,
    sector_name     TEXT,
    close           REAL,
    ma_50           REAL,
    ma_200          REAL,
    rsi_14          REAL,
    pct_above_ma50  REAL,
    pct_above_ma200 REAL,
    reason          TEXT,
    UNIQUE (run_date, symbol)
)
"""

_INSERT_SQL = """\
INSERT OR IGNORE INTO {table}
    (run_date, run_timestamp, symbol, sector_name, close, ma_50, ma_200,
     rsi_14, pct_above_ma50, pct_above_ma200, reason)
VALUES
    (:run_date, :run_timestamp, :symbol, :sector_name, :close, :ma_50, :ma_200,
     :rsi_14, :pct_above_ma50, :pct_above_ma200, :reason)
"""


class ScreeningDatabase:
    """Manages the SQLite database that stores daily screening candidates."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        """Create all three candidate tables if they do not yet exist."""
        with self._connect() as conn:
            for table in _STRATEGY_TABLE.values():
                conn.execute(_CREATE_TABLE_SQL.format(table=table))
        LOGGER.debug("Database initialised at %s", self._db_path)

    # ── Public API ────────────────────────────────────────────────────────────

    def save_candidates(
        self,
        candidates: list[dict[str, Any]],
        strategy: str,
        run_date: str,
        run_timestamp: str,
    ) -> int:
        """Persist *candidates* for *strategy* on *run_date*.

        Duplicate (run_date, symbol) pairs are silently skipped so that
        re-running the job on the same day is safe.

        Returns the number of rows actually inserted.
        """
        table = _STRATEGY_TABLE.get(strategy)
        if table is None:
            LOGGER.warning("Unknown strategy '%s' — skipping DB save", strategy)
            return 0

        rows = [_build_row(c, run_date, run_timestamp) for c in candidates]
        if not rows:
            return 0

        sql = _INSERT_SQL.format(table=table)
        with self._connect() as conn:
            cursor = conn.executemany(sql, rows)
            inserted = cursor.rowcount

        LOGGER.info(
            "DB: saved %d / %d candidates to '%s' (run_date=%s)",
            inserted, len(rows), table, run_date,
        )
        return inserted

    def get_candidates(
        self,
        strategy: str,
        run_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return stored candidates for *strategy*, optionally filtered by *run_date*."""
        table = _STRATEGY_TABLE.get(strategy)
        if table is None:
            LOGGER.warning("Unknown strategy '%s'", strategy)
            return []

        # Guard against injection: table name must be one of the three known values.
        if table not in _VALID_TABLES:
            raise ValueError(f"Unexpected table name: {table!r}")

        if run_date:
            sql = f"SELECT * FROM {table} WHERE run_date = ? ORDER BY run_date, symbol"
            params: tuple[Any, ...] = (run_date,)
        else:
            sql = f"SELECT * FROM {table} ORDER BY run_date, symbol"
            params = ()

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [dict(r) for r in rows]


# ── Module-level helpers ──────────────────────────────────────────────────────


def _build_row(
    candidate: dict[str, Any],
    run_date: str,
    run_timestamp: str,
) -> dict[str, Any]:
    """Convert a raw candidate dict to a flat row dict for DB insertion."""
    indicators = candidate.get("indicators", {})
    close  = _to_float(indicators.get("close"))
    ma_50  = _to_float(indicators.get("ma_50"))
    ma_200 = _to_float(indicators.get("ma_200"))
    rsi_14 = _to_float(indicators.get("rsi_14"))

    pct_above_ma50 = _pct_above(close, ma_50)
    pct_above_ma200 = _pct_above(close, ma_200)

    return {
        "run_date":        run_date,
        "run_timestamp":   run_timestamp,
        "symbol":          candidate.get("symbol", "UNKNOWN"),
        "sector_name":     candidate.get("sector_name", ""),
        "close":           close,
        "ma_50":           ma_50,
        "ma_200":          ma_200,
        "rsi_14":          rsi_14,
        "pct_above_ma50":  pct_above_ma50,
        "pct_above_ma200": pct_above_ma200,
        "reason":          candidate.get("reason", ""),
    }


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_above(close: float | None, ma: float | None) -> float | None:
    if close is None or ma is None or ma == 0:
        return None
    return ((close / ma) - 1.0) * 100.0
