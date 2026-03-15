"""Public market data retrieval using Yahoo Finance."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf


class DataFetcher:
    """Fetch stock data from Yahoo Finance without authentication."""

    def get_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """Fetch OHLCV history for one symbol.

        Args:
            symbol: Ticker symbol such as AAPL.
            period: Range period supported by yfinance, for example 1y or 6mo.
            interval: Candle interval, for example 1d or 1h.

        Returns:
            A DataFrame with OHLCV columns.

        Raises:
            ValueError: If no data can be retrieved for the symbol.
        """
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            raise ValueError(f"Failed to fetch OHLCV for {symbol}: {exc}") from exc

        if df.empty:
            raise ValueError(f"No OHLCV data returned for symbol '{symbol}'")

        return df

    def get_info(self, symbol: str) -> dict[str, Any]:
        """Fetch metadata for a ticker symbol."""
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

    def get_watchlist(self, watchlist_file: str = "data/watchlist.txt") -> list[str]:
        """Load ticker symbols from a plain-text watchlist file."""
        path = Path(watchlist_file)
        if not path.exists():
            raise ValueError(f"Watchlist file does not exist: {watchlist_file}")

        symbols = [line.strip().upper() for line in path.read_text().splitlines() if line.strip()]
        if not symbols:
            raise ValueError(f"Watchlist file is empty: {watchlist_file}")

        return symbols
