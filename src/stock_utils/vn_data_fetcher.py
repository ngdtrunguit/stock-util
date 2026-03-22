"""VN market data utilities powered by vnstock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

import pandas as pd
import yfinance as yf
from vnstock import Listing, Vnstock, change_api_key


class VnDataFetcher:
    """Fetch VN sectors/symbols and OHLCV using vnstock."""

    def __init__(self, source: str = "VCI") -> None:
        self.source = source
        api_key = os.getenv("VNSTOCK_API_KEY", "").strip()
        if api_key:
            try:
                change_api_key(api_key)
            except Exception:
                # Keep local/runtime behavior resilient even when key setup fails.
                pass

    def get_sector_symbols(self) -> list[dict[str, Any]]:
        """Return sector records with id, name, and symbol list."""
        df = Listing().symbols_by_industries()
        if df is None or df.empty:
            return []

        sectors: list[dict[str, Any]] = []
        grouped = df.groupby(["industry_code", "industry_name"], as_index=False)
        for (industry_code, industry_name), group in grouped:
            symbols = sorted({str(s).strip().upper() for s in group["symbol"].dropna().tolist() if str(s).strip()})
            if not symbols:
                continue
            sectors.append(
                {
                    "id": int(industry_code),
                    "name": str(industry_name),
                    "symbols": symbols,
                }
            )

        sectors.sort(key=lambda item: item["name"])
        return sectors

    def get_ohlcv(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Fetch daily OHLCV with Yahoo Finance primary and vnstock fallback.

        Returns columns: Date, Open, High, Low, Close, Volume
        """
        yahoo_df = self.get_ohlcv_yahoo(symbol=symbol, days=days)
        if not yahoo_df.empty:
            return yahoo_df
        return self.get_ohlcv_vnstock(symbol=symbol, days=days)

    @staticmethod
    def to_yahoo_symbol(symbol: str) -> str:
        base = symbol.strip().upper()
        if not base:
            return base
        if "." in base:
            return base
        return f"{base}.VN"

    def get_ohlcv_yahoo(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Fetch VN OHLCV from Yahoo Finance using *.VN symbols."""
        ticker_symbol = self.to_yahoo_symbol(symbol)
        period = "1y" if days <= 365 else "2y" if days <= 730 else "5y"
        df = yf.Ticker(ticker_symbol).history(period=period, interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        out = df.reset_index().rename(columns={"Date": "Date"}).copy()
        for column in ("Open", "High", "Low", "Close", "Volume"):
            if column in out.columns:
                out[column] = pd.to_numeric(out[column], errors="coerce")

        out = out[[c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in out.columns]]
        out = out.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).sort_values("Date")
        if len(out) > days:
            out = out.iloc[-days:]
        return out.reset_index(drop=True)

    def get_ohlcv_vnstock(self, symbol: str, days: int = 365) -> pd.DataFrame:
        """Fetch daily OHLCV from vnstock and normalize columns."""
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=max(30, days + 30))

        raw = (
            Vnstock()
            .stock(symbol=symbol.strip().upper(), source=self.source)
            .quote.history(start=start.isoformat(), end=end.isoformat(), interval="1D")
        )

        if raw is None or raw.empty:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])

        # vnstock history columns are lowercase by default.
        rename_map = {
            "time": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
        df = raw.rename(columns=rename_map).copy()

        for column in ("Open", "High", "Low", "Close", "Volume"):
            if column in df.columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

        if "Date" in df.columns:
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

        df = df[[c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]]
        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).sort_values("Date")

        if len(df) > days:
            df = df.iloc[-days:]

        return df.reset_index(drop=True)

    @staticmethod
    def _period_to_days(period: str) -> int:
        mapping = {
            "1mo": 30,
            "3mo": 90,
            "6mo": 200,
            "1y": 365,
            "2y": 730,
            "5y": 1825,
        }
        return mapping.get(str(period).lower(), 365)

    def get_ohlcv_for_daily_job(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        """Return OHLCV indexed by Date for compatibility with existing daily job."""
        if str(interval).lower() != "1d":
            raise ValueError(f"Unsupported interval for VN fetcher: {interval}")
        days = self._period_to_days(period)
        df = self.get_ohlcv(symbol=symbol, days=days)
        if df.empty:
            raise ValueError(f"No OHLCV data returned for symbol {symbol!r}")
        return df.set_index("Date")

    def get_ohlcv_bulk(
        self,
        symbols: list[str],
        period: str = "1y",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """Bulk fetch with sequential fallback suitable for smaller VN universes and API limits."""
        if not symbols:
            return {}
        unique = list(dict.fromkeys(s.upper() for s in symbols))
        result: dict[str, pd.DataFrame] = {}
        for symbol in unique:
            try:
                result[symbol] = self.get_ohlcv_for_daily_job(symbol=symbol, period=period, interval=interval)
            except Exception:
                continue
        return result
