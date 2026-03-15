"""Technical indicators and candidate rules for stock screening."""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta


def add_core_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of dataframe enriched with key technical indicators."""
    out = df.copy()
    out["MA_200"] = ta.sma(out["Close"], length=200)
    out["EMA_20"] = ta.ema(out["Close"], length=20)
    out["EMA_50"] = ta.ema(out["Close"], length=50)
    out["RSI_14"] = ta.rsi(out["Close"], length=14)

    macd = ta.macd(out["Close"], fast=12, slow=26, signal=9)
    if macd is not None and not macd.empty:
        out["MACD"] = macd["MACD_12_26_9"]
        out["MACD_SIGNAL"] = macd["MACDs_12_26_9"]
        out["MACD_HIST"] = macd["MACDh_12_26_9"]

    out["ATR_14"] = ta.atr(out["High"], out["Low"], out["Close"], length=14)
    out["VOL_SMA_20"] = ta.sma(out["Volume"], length=20)
    return out


def is_candidate(df: pd.DataFrame) -> bool:
    """Evaluate candidate rules on the latest two rows.

    Rules:
    - RSI extreme: RSI < 30 or RSI > 70
    - Bullish EMA crossover: EMA20 crosses above EMA50 on latest bar
    - Volume confirmation: latest volume above 20-day average
    """
    if len(df) < 60:
        return False

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    rsi_ok = bool((latest.get("RSI_14", 50) < 30) or (latest.get("RSI_14", 50) > 70))
    crossover = bool(prev.get("EMA_20", 0) <= prev.get("EMA_50", 0) and latest.get("EMA_20", 0) > latest.get("EMA_50", 0))
    volume_ok = bool(latest.get("Volume", 0) > latest.get("VOL_SMA_20", float("inf")))

    return rsi_ok and crossover and volume_ok


def is_price_above_ma200(df: pd.DataFrame) -> bool:
    """Return True when the latest close is above the 200-day moving average."""
    if len(df) < 200:
        return False

    latest = df.iloc[-1]
    close = latest.get("Close")
    ma_200 = latest.get("MA_200")

    if pd.isna(close) or pd.isna(ma_200):
        return False

    return bool(close > ma_200)
