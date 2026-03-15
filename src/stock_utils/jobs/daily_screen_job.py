"""Daily screening orchestration entrypoint."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from stock_utils.ai_agent_client import TradingAnalysisAgent
from stock_utils.config import Settings
from stock_utils.data_fetcher import DataFetcher
from stock_utils.screener import Screener
from stock_utils.telegram_notifier import send_markdown_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)

OUTPUT_DIR = Path("data/output")
DAILY_CANDIDATES_JSON = OUTPUT_DIR / "daily-candidates.json"
DAILY_CANDIDATES_MD = OUTPUT_DIR / "daily-candidates.md"


def _fmt_float(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    indicators = candidate.get("indicators", {})
    close = indicators.get("close")
    ma_200 = indicators.get("ma_200")

    pct_above_ma200: float | None = None
    try:
        close_value = float(close)
        ma_200_value = float(ma_200)
        if ma_200_value != 0:
            pct_above_ma200 = ((close_value / ma_200_value) - 1.0) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        pct_above_ma200 = None

    return {
        "symbol": candidate.get("symbol", "UNKNOWN"),
        "reason": candidate.get("reason", ""),
        "close": close,
        "ma_200": ma_200,
        "pct_above_ma200": pct_above_ma200,
    }


def _build_markdown_report(candidates: list[dict[str, Any]], run_date: str) -> str:
    lines = [
        f"# Daily Candidates - {run_date}",
        "",
        "Rule: Daily close above MA200.",
        "",
        f"Candidates found: {len(candidates)}",
        "",
    ]

    if not candidates:
        lines.append("No candidates matched the rule.")
        return "\n".join(lines)

    lines.extend(
        [
            "| Symbol | Close | MA200 | % Above MA200 |",
            "|--------|-------|-------|---------------|",
        ]
    )

    for candidate in candidates:
        payload = _candidate_payload(candidate)
        pct_text = "n/a"
        if payload["pct_above_ma200"] is not None:
            pct_text = f"{payload['pct_above_ma200']:+.2f}%"
        lines.append(
            f"| {payload['symbol']} | {_fmt_float(payload['close'])} | {_fmt_float(payload['ma_200'])} | {pct_text} |"
        )

    return "\n".join(lines)


def _build_telegram_message(
    candidates: list[dict[str, Any]],
    run_date: str,
    ai_summary: str | None = None,
) -> str:
    lines = [
        f"📈 *Daily Screen* — {run_date}",
        "Rule: Close > MA200 on D1",
        f"Candidates: {len(candidates)}",
        "",
    ]

    if not candidates:
        lines.append("No candidates matched today's rule.")
    else:
        for candidate in candidates:
            payload = _candidate_payload(candidate)
            pct_text = "n/a"
            if payload["pct_above_ma200"] is not None:
                pct_text = f"{payload['pct_above_ma200']:+.2f}%"
            lines.append(
                f"- *{payload['symbol']}*: Close {_fmt_float(payload['close'])} | MA200 {_fmt_float(payload['ma_200'])} | {pct_text}"
            )

    if ai_summary:
        lines.extend(["", "*AI Analysis*", ai_summary])

    return "\n".join(lines)


def _write_output_files(candidates: list[dict[str, Any]], run_timestamp: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_date = run_timestamp[:10]
    payload = {
        "generated_at": run_timestamp,
        "strategy": "price_above_ma200",
        "timeframe": "1d",
        "candidate_count": len(candidates),
        "candidates": [_candidate_payload(candidate) for candidate in candidates],
    }

    DAILY_CANDIDATES_JSON.write_text(json.dumps(payload, indent=2))
    DAILY_CANDIDATES_MD.write_text(_build_markdown_report(candidates, run_date))

    LOGGER.info("Wrote candidate snapshot to %s", DAILY_CANDIDATES_JSON)
    LOGGER.info("Wrote candidate report to %s", DAILY_CANDIDATES_MD)


def main() -> None:
    """Run daily screener, summarize results, and publish to Telegram."""
    load_dotenv()
    settings = Settings.from_env()

    LOGGER.info("Starting daily screen job")

    data_fetcher = DataFetcher()
    screener = Screener(
        data_fetcher=data_fetcher,
        watchlist_file=settings.watchlist_file,
        period="1y",
        interval="1d",
        strategy="price_above_ma200",
    )

    LOGGER.info("Running symbol screen")
    candidates = screener.run_screen()

    run_timestamp = datetime.now(timezone.utc).isoformat()
    run_date = run_timestamp[:10]
    _write_output_files(candidates, run_timestamp)

    ai_summary: str | None = None
    if candidates and settings.project_endpoint and settings.agent_name:
        LOGGER.info("Summarizing %d candidates", len(candidates))
        ai_agent = TradingAnalysisAgent(
            project_endpoint=settings.project_endpoint,
            agent_name=settings.agent_name,
        )
        ai_summary = ai_agent.summarize_screening_results(candidates)

    summary = _build_telegram_message(candidates, run_date, ai_summary)

    LOGGER.info("Sending Telegram notification")
    send_markdown_message(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        text=summary,
    )

    LOGGER.info("Daily screen job completed")


if __name__ == "__main__":
    main()
