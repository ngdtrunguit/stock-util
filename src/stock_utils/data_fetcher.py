"""Public market data retrieval using Yahoo Finance."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from stock_utils.paths import DEFAULT_WATCHLIST_FILE, resolve_repo_path

LOGGER = logging.getLogger(__name__)


class DataFetcher:
    """Fetch stock data from Yahoo Finance without authentication."""

    def get_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            raise ValueError(f"Failed to fetch OHLCV for {symbol}: {exc}") from exc
        if df.empty:
            raise ValueError(f"No OHLCV data returned for symbol {symbol!r}")
        return df

    def get_ohlcv_bulk(
        self,
        symbols: list[str],
        period: str = "1y",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """Bulk-fetch OHLCV via yf.download() -- one network round-trip for all symbols."""
        if not symbols:
            return {}
        unique = list(dict.fromkeys(s.upper() for s in symbols))
        LOGGER.info("yf.download: fetching %d symbols", len(unique))
        try:
            raw = yf.download(
                tickers=unique,
                period=period,
                interval=interval,
                auto_adjust=False,
                progress=False,
                group_by="ticker",
                threads=True,
            )
        except Exception as exc:
            LOGGER.warning("Bulk download failed: %s", exc)
            return {}
        if raw is None or raw.empty:
            return {}
        result: dict[str, pd.DataFrame] = {}
        for sym in unique:
            try:
                df = raw[sym].dropna(how="all")
                if not df.empty:
                    result[sym] = df
            except (KeyError, TypeError):
                pass
        LOGGER.info("Bulk fetch complete: %d / %d symbols", len(result), len(unique))
        return result

    def get_info(self, symbol: str) -> dict[str, Any]:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info or {}
        except Exception as exc:
            raise ValueError(f"Failed to fetch info for {symbol}: {exc}") from exc
        return {
            "symbol": symbol,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "marketCap": info.get("marketCap"),
            "longName": info.get("longName"),
        }

    def get_watchlist(self, watchlist_file: str = str(DEFAULT_WATCHLIST_FILE)) -> list[str]:
        path = resolve_repo_path(watchlist_file)
        if not path.exists():
            raise ValueError(f"Watchlist file does not exist: {path}")
        symbols = [line.strip().upper() for line in path.read_text().splitlines() if line.strip()]
        if not symbols:
            raise ValueError(f"Watchlist file is empty: {path}")
        return symbols
