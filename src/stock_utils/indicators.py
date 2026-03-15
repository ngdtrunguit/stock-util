"""Technical indicators and candidate rules for stock screening.

All indicators are implemented with plain pandas to avoid third-party TA library
dependencies and Python-version constraints.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Low-level indicator helpers
# ---------------------------------------------------------------------------

def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length, min_periods=length).mean()


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's smoothed RSI."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, min_periods=length).mean()
    avg_loss = loss.ewm(com=length - 1, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(com=length - 1, min_periods=length).mean()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def add_core_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of dataframe enriched with key technical indicators."""
    out = df.copy()
    out["MA_200"] = _sma(out["Close"], 200)
    out["MA_50"]  = _sma(out["Close"], 50)
    out["EMA_20"] = _ema(out["Close"], 20)
    out["EMA_50"] = _ema(out["Close"], 50)
    out["RSI_14"] = _rsi(out["Close"], 14)

    macd_line, signal_line, hist = _macd(out["Close"])
    out["MACD"]        = macd_line
    out["MACD_SIGNAL"] = signal_line
    out["MACD_HIST"]   = hist

    out["ATR_14"]    = _atr(out["High"], out["Low"], out["Close"], 14)
    out["VOL_SMA_20"] = _sma(out["Volume"], 20)
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


def is_price_above_ma50_and_ma200(df: pd.DataFrame) -> bool:
    """Return True when the latest close is above both MA50 and MA200."""
    if len(df) < 200:
        return False

    latest = df.iloc[-1]
    close  = latest.get("Close")
    ma_50  = latest.get("MA_50")
    ma_200 = latest.get("MA_200")

    if pd.isna(close) or pd.isna(ma_50) or pd.isna(ma_200):
        return False

    return bool(float(close) > float(ma_50) and float(close) > float(ma_200))


def is_golden_cross_weekly_candidate(df: pd.DataFrame) -> bool:
    """Return True when all three conditions hold on the D1 timeframe:

    1. Close > MA200  (uptrend filter)
    2. Close > MA50   (momentum filter)
    3. MA50 crossed above MA200 within the last 5 trading days (golden cross recency)
    """
    if len(df) < 200:
        return False

    latest = df.iloc[-1]
    close  = latest.get("Close")
    ma_200 = latest.get("MA_200")
    ma_50  = latest.get("MA_50")

    if pd.isna(close) or pd.isna(ma_200) or pd.isna(ma_50):
        return False

    # Conditions 1 & 2 — price above both moving averages
    if not (float(close) > float(ma_200) and float(close) > float(ma_50)):
        return False

    # Condition 3 — MA50 crossed above MA200 in the last 5 bars (1 trading week)
    # We need 6 rows to evaluate 5 consecutive transitions.
    window = df.iloc[-6:]
    for i in range(1, len(window)):
        prev = window.iloc[i - 1]
        curr = window.iloc[i]
        p50, p200 = prev.get("MA_50"), prev.get("MA_200")
        c50, c200 = curr.get("MA_50"), curr.get("MA_200")
        if any(pd.isna(v) for v in (p50, p200, c50, c200)):
            continue
        if float(p50) <= float(p200) and float(c50) > float(c200):  # type: ignore[arg-type]
            return True

    return False
