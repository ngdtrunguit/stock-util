"""VN market data utilities powered by vnstock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

import pandas as pd
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
        """Fetch daily OHLCV and normalize columns to US pipeline shape.

        Returns columns: Date, Open, High, Low, Close, Volume
        """
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
