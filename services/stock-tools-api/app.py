"""Stock Tools API — FastAPI service for Azure AI Foundry agent integration.

Exposes three endpoints that Azure AI Foundry agents (e.g. finrobot stock
analyst) can call with a ticker symbol received from the daily screening job:

  POST /price_history   — OHLCV history + current price (yfinance)
  POST /technicals      — RSI, MA50/MA200 premium, volatility, trend
  POST /news_sentiment  — Recent headlines + simple sentiment score (Yahoo Finance)

All data sources are free and open — no third-party API keys required.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator

app = FastAPI(
    title="Stock Tools API",
    description="Technical analysis and news sentiment tools for stock tickers.",
    version="1.0.0",
)


def _downgrade_schema_to_30(schema: dict) -> dict:
    """Recursively convert OpenAPI 3.1.0 constructs to 3.0.0 equivalents.

    Azure AI Foundry Agents API rejects 3.1.0 specs.  The main differences:
    - Optional[X]  → anyOf:[{type:X},{type:null}]  must become  {type:X, nullable:true}
    - {type:null}  standalone becomes {nullable:true}
    """
    if not isinstance(schema, dict):
        return schema

    # Handle anyOf with exactly one non-null type + {type: null}  →  nullable
    if "anyOf" in schema:
        any_of: list = schema["anyOf"]
        null_entries = [s for s in any_of if s == {"type": "null"} or s.get("type") == "null"]
        non_null_entries = [s for s in any_of if s not in null_entries and s.get("type") != "null"]
        if null_entries and len(non_null_entries) == 1:
            merged = {**non_null_entries[0], "nullable": True}
            # Preserve any sibling keys (title, default, …)
            for k, v in schema.items():
                if k != "anyOf":
                    merged.setdefault(k, v)
            schema = _downgrade_schema_to_30(merged)
            return schema
        elif null_entries and len(non_null_entries) == 0:
            # pure null type
            schema = {k: v for k, v in schema.items() if k != "anyOf"}
            schema["nullable"] = True

    # Recurse into all nested dicts/lists
    return {
        k: (
            _downgrade_schema_to_30(v)
            if isinstance(v, dict)
            else [_downgrade_schema_to_30(i) if isinstance(i, dict) else i for i in v]
            if isinstance(v, list)
            else v
        )
        for k, v in schema.items()
    }


def _strip_validation_schemas(schema: dict) -> dict:
    """Remove FastAPI 422 validation-error responses and their component schemas.

    Azure AI Foundry rejects specs with anyOf multi-type unions or bare-title
    properties found inside the auto-generated ValidationError component.
    Agents never need to parse 422 error bodies.
    """
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.get("responses", {}).pop("422", None)

    schemas = schema.get("components", {}).get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        schemas.pop(name, None)

    return schema


def _custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    schema["openapi"] = "3.0.0"
    schema = _downgrade_schema_to_30(schema)
    schema = _strip_validation_schemas(schema)
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi  # type: ignore[method-assign]


# ── Request models ────────────────────────────────────────────────────────────


class TickerRequest(BaseModel):
    ticker: str = Field(default="TSLA", min_length=1, max_length=10)
    days: int = Field(default=180, ge=1, le=730)


class HistoryRequest(BaseModel):
    history: list[dict] = Field(default_factory=list)


# ── Constants ─────────────────────────────────────────────────────────────────

MAX_HEADLINES = 10  # Maximum number of news articles to analyse per request


# API key used by Azure AI Foundry agent when calling tool endpoints.
API_KEY_ENV = 'STOCK_TOOLS_API_KEY'
EXPECTED_API_KEY = os.getenv(API_KEY_ENV, '').strip()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_float(value: object) -> float | None:
    """Return a Python float or None for NaN / non-numeric values."""
    try:
        f = float(value)  # type: ignore[arg-type]
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _require_api_key(
    x_api_key: str | None = Header(default=None, alias='X-API-Key'),
    q_x_api_key: str | None = Query(default=None, alias='X-API-Key'),
    q_x_api_key_lc: str | None = Query(default=None, alias='x-api-key'),
    q_api_key: str | None = Query(default=None, alias='api_key'),
    q_apikey: str | None = Query(default=None, alias='apikey'),
) -> None:
    """Require a valid API key for tool endpoints."""
    provided_key = x_api_key or q_x_api_key or q_x_api_key_lc or q_api_key or q_apikey
    if not EXPECTED_API_KEY:
        raise HTTPException(status_code=503, detail=f'{API_KEY_ENV} is not configured')
    if provided_key != EXPECTED_API_KEY:
        raise HTTPException(status_code=401, detail='Invalid or missing API key')


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict:
    """Liveness probe for the Container App health check."""
    return {"status": "ok"}


@app.post("/price_history")
def get_price_history(req: TickerRequest, _: None = Depends(_require_api_key)) -> dict:
    """Download OHLCV history from Yahoo Finance and return the last 30 rows."""
    ticker = req.ticker.upper().strip()
    end = datetime.now()
    start = end - timedelta(days=req.days)

    data: pd.DataFrame = yf.download(
        ticker, start=start, end=end, progress=False, auto_adjust=True
    )
    if data.empty:
        raise HTTPException(status_code=404, detail=f"No data found for ticker '{ticker}'")

    # Flatten MultiIndex columns produced by yfinance when auto_adjust=True
    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.get_level_values(0)

    current_price = _safe_float(data["Close"].iloc[-1])
    history = (
        data[["Open", "High", "Low", "Close", "Volume"]]
        .tail(30)
        .reset_index()
        .rename(columns={"index": "Date"})
        .assign(Date=lambda df: df["Date"].astype(str))
        .to_dict("records")
    )
    return {"ticker": ticker, "current_price": current_price, "history": history}


@app.post("/technicals")
def compute_technicals(history_data: HistoryRequest, _: None = Depends(_require_api_key)) -> dict:
    """Compute RSI, MA50/MA200 premium %, annualised volatility and trend."""
    records = history_data.history
    if not records:
        raise HTTPException(status_code=422, detail="history list is empty")

    df = pd.DataFrame(records)
    if "Close" not in df.columns:
        raise HTTPException(status_code=422, detail="history records must contain a 'Close' field")

    close: pd.Series = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(close) < 2:
        raise HTTPException(status_code=422, detail="Not enough data points to compute indicators")

    rsi_val = _safe_float(RSIIndicator(close, window=14).rsi().iloc[-1])

    ma50_val = (
        _safe_float(SMAIndicator(close, window=50).sma_indicator().iloc[-1])
        if len(close) >= 50
        else None
    )
    ma200_val = (
        _safe_float(SMAIndicator(close, window=200).sma_indicator().iloc[-1])
        if len(close) >= 200
        else None
    )

    current = _safe_float(close.iloc[-1])

    ma50_premium = (
        round((current - ma50_val) / ma50_val * 100, 2)
        if current is not None and ma50_val is not None and ma50_val != 0
        else 0.0
    )
    ma200_premium = (
        round((current - ma200_val) / ma200_val * 100, 2)
        if current is not None and ma200_val is not None and ma200_val != 0
        else 0.0
    )

    vol = close.pct_change().tail(20).std()
    volatility = round(float(vol) * np.sqrt(252) * 100, 2) if not np.isnan(vol) else 0.0

    if current is not None and ma50_val is not None and ma200_val is not None:
        trend = "up" if current > ma50_val > ma200_val else (
            "down" if current < ma50_val else "sideways"
        )
    elif current is not None and ma50_val is not None:
        trend = "up" if current > ma50_val else "down"
    else:
        trend = "unknown"

    return {
        "rsi": rsi_val,
        "ma50_premium_pct": ma50_premium,
        "ma200_premium_pct": ma200_premium,
        "volatility_20d_pct": volatility,
        "trend": trend,
    }


@app.post("/news_sentiment")
def get_news_sentiment(req: TickerRequest, _: None = Depends(_require_api_key)) -> dict:
    """Fetch recent headlines from Yahoo Finance and return a simple sentiment score.

    Uses ``yfinance.Ticker.news`` — no API key required.
    """
    ticker = req.ticker.upper().strip()

    try:
        articles = yf.Ticker(ticker).news or []
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Yahoo Finance news fetch failed: {exc}") from exc

    # Each article dict has at least: title, publisher, link, providerPublishTime
    cutoff_ts = (datetime.now() - timedelta(days=30)).timestamp()
    recent = [
        a for a in articles
        if isinstance(a.get("providerPublishTime"), (int, float))
        and a["providerPublishTime"] >= cutoff_ts
    ]

    headlines = [
        a["title"] for a in recent[:MAX_HEADLINES]
        if isinstance(a.get("title"), str) and a["title"].strip()
    ]

    positive_words = {"beat", "growth", "buy", "upgrade", "profit", "gain"}
    negative_words = {"miss", "decline", "sell", "downgrade", "loss", "drop"}

    pos = sum(
        sum(1 for w in positive_words if w in h.lower()) for h in headlines
    )
    neg = sum(
        sum(1 for w in negative_words if w in h.lower()) for h in headlines
    )
    score = round((pos - neg) / max(len(headlines), 1), 2)

    return {
        "ticker": ticker,
        "headlines": headlines[:5],
        "sentiment_score": score,
        "positive_signals": pos,
        "negative_signals": neg,
        "source": "Yahoo Finance",
    }
