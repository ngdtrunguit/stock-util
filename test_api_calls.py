"""Call the deployed Stock Tools API for one or more tickers and print results.

Usage (local):
    STOCK_TOOLS_API_KEY=<key> python test_api_calls.py
    STOCK_TOOLS_API_KEY=<key> python test_api_calls.py AAPL NVDA

Usage (CI — key is injected via env / secret):
    python test_api_calls.py TSLA MSFT
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from requests import Response

BASE_URL = os.getenv(
    "STOCK_TOOLS_API_BASE_URL",
    "https://stock-tools-api-dev-app.calmstone-a9644956.eastus.azurecontainerapps.io",
)
API_KEY = os.getenv("STOCK_TOOLS_API_KEY", "")
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def _headers() -> dict[str, str]:
    if API_KEY:
        return {"X-API-Key": API_KEY, "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


def _request_with_retries(
    method: str,
    path: str,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
    **kwargs: Any,
) -> Response:
    url = f"{BASE_URL}{path}"
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return requests.request(
                method,
                url,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                **kwargs,
            )
        except RETRYABLE_EXCEPTIONS as exc:
            last_error = exc
            if attempt == attempts:
                break
            sleep_for = backoff_seconds * attempt
            print(
                f"  {method.upper()} {path} attempt {attempt}/{attempts} failed: {exc}. "
                f"Retrying in {sleep_for:.0f}s..."
            )
            time.sleep(sleep_for)

    assert last_error is not None
    raise last_error


def check_health() -> bool:
    resp = _request_with_retries("GET", "/health", attempts=4, backoff_seconds=10)
    resp.raise_for_status()
    data = resp.json()
    print(f"  /health → {data}")
    return data.get("status") == "ok"


def post_price_history(ticker: str, days: int = 180) -> dict[str, Any]:
    resp = _request_with_retries(
        "POST",
        "/price_history",
        headers=_headers(),
        json={"ticker": ticker, "days": days},
    )
    resp.raise_for_status()
    return resp.json()


def post_technicals(history: list[dict]) -> dict[str, Any]:
    resp = _request_with_retries(
        "POST",
        "/technicals",
        headers=_headers(),
        json={"history": history},
    )
    resp.raise_for_status()
    return resp.json()


def post_news_sentiment(ticker: str, days: int = 30) -> dict[str, Any]:
    resp = _request_with_retries(
        "POST",
        "/news_sentiment",
        headers=_headers(),
        json={"ticker": ticker, "days": days},
    )
    resp.raise_for_status()
    return resp.json()


def run_pipeline(ticker: str) -> None:
    sep = "=" * 68
    print(f"\n{sep}")
    print(f"  TICKER: {ticker}")
    print(sep)

    # ── 1 / price_history ────────────────────────────────────────────────
    print("\n[1/3] POST /price_history  (days=180)")
    ph = post_price_history(ticker, days=180)
    print(f"  current_price    : ${ph['current_price']:,.2f}")
    print(f"  history rows     : {len(ph['history'])} (last-30 of 180-day window)")
    if ph["history"]:
        first = ph["history"][0]
        last = ph["history"][-1]
        print(f"  date range       : {first['Date']}  →  {last['Date']}")
        print(
            "  latest OHLCV row : "
            f"{json.dumps({k: round(v, 2) if isinstance(v, float) else v for k, v in last.items()})}"
        )

    # ── 2 / technicals ───────────────────────────────────────────────────
    print("\n[2/3] POST /technicals")
    tech = post_technicals(ph["history"])
    rsi = tech.get("rsi")
    rsi_label = (
        "(overbought >70)" if rsi and rsi > 70 else "(oversold <30)" if rsi and rsi < 30 else "(neutral)"
    )
    print(f"  rsi              : {rsi:.1f}  {rsi_label}" if rsi else f"  rsi              : {rsi}")
    print(f"  ma50_premium     : {tech['ma50_premium_pct']:+.2f}%   (price vs 50-day MA)")
    print(f"  ma200_premium    : {tech['ma200_premium_pct']:+.2f}%   (price vs 200-day MA)")
    print(f"  volatility_20d   : {tech['volatility_20d_pct']:.1f}%  (annualised)")
    print(f"  trend            : {tech['trend']}")

    # ── 3 / news_sentiment ───────────────────────────────────────────────
    print("\n[3/3] POST /news_sentiment  (days=30)")
    news = post_news_sentiment(ticker, days=30)
    score = news.get("sentiment_score", 0)
    sentiment_label = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    print(f"  sentiment_score  : {score:+.2f}  ({sentiment_label})")
    print(f"  positive_signals : {news['positive_signals']}")
    print(f"  negative_signals : {news['negative_signals']}")
    print(f"  source           : {news['source']}")
    headlines = news.get("headlines", [])
    print(f"  headlines ({len(headlines)}):")
    for h in headlines:
        print(f"    • {h}")

    print(f"\n{sep}\n")


def main() -> None:
    tickers = [t.upper() for t in sys.argv[1:]] if len(sys.argv) > 1 else ["TSLA", "MSFT"]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print("Stock Tools API — validation run")
    print(f"Base URL  : {BASE_URL}")
    print(f"Tickers   : {tickers}")
    print(f"Timestamp : {ts}")

    if not API_KEY:
        print("\nWARNING: STOCK_TOOLS_API_KEY is not set — API calls will return 401.")

    print("\n--- Health check ---")
    check_health()

    for ticker in tickers:
        run_pipeline(ticker)


if __name__ == "__main__":
    main()
