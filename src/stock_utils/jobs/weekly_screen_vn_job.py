"""Weekly VN screen: evaluate VN symbols grouped by VN sectors only."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from stock_utils.ai_agent_client import TradingAnalysisAgent
from stock_utils.config import Settings
from stock_utils.indicators import add_core_indicators, is_candidate
from stock_utils.telegram_notifier import send_markdown_message
from stock_utils.vn_data_fetcher import VnDataFetcher
from stock_utils.vn_paths import VN_SECTORS_DIR, VN_SECTORS_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)

MAX_STOCKS_PER_SECTOR = 80
MAX_HISTORY_CALLS = int(os.getenv("VN_WEEKLY_MAX_HISTORY_CALLS", "15"))


def _fmt_float(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def build_candidates_table(candidates: list[dict[str, Any]]) -> str:
    header = "| Sector | Symbol | Close | RSI | EMA20 | EMA50 | Vol/SMA | MACD | ATR |"
    sep = "|--------|--------|-------|-----|-------|-------|---------|------|-----|"
    lines = [header, sep]
    for c in candidates:
        ind = c.get("indicators", {})
        vol_ratio = (
            f"{ind['volume'] / ind['volume_sma_20']:.2f}x"
            if ind.get("volume") and ind.get("volume_sma_20")
            else "n/a"
        )
        lines.append(
            f"| {c.get('sector', '')} "
            f"| {c.get('symbol', '')} "
            f"| {_fmt_float(ind.get('close'))} "
            f"| {_fmt_float(ind.get('rsi_14'))} "
            f"| {_fmt_float(ind.get('ema_20'))} "
            f"| {_fmt_float(ind.get('ema_50'))} "
            f"| {vol_ratio} "
            f"| {_fmt_float(ind.get('macd'))} "
            f"| {_fmt_float(ind.get('atr_14'))} |"
        )
    return "\n".join(lines)


def build_summary_table(sector_results: list[dict[str, Any]]) -> str:
    header = "| Sector | Stocks Scanned | Candidates |"
    sep = "|--------|---------------|------------|"
    lines = [header, sep]
    for r in sector_results:
        lines.append(
            f"| {r['sector_name']} | {r['stocks_scanned']} | {r['candidates_found']} |"
        )
    return "\n".join(lines)


def load_vn_sectors() -> list[dict[str, Any]]:
    if not VN_SECTORS_FILE.exists():
        LOGGER.warning("sectors-vn.json not found; run monthly_sector_vn_job first")
        return []
    raw = json.loads(VN_SECTORS_FILE.read_text())
    sectors: list[dict[str, Any]] = raw.get("sectors", [])
    return sectors


def _load_sector_symbols(sector_id: int) -> list[str]:
    matches = list(VN_SECTORS_DIR.glob(f"{sector_id}-*.json"))
    if not matches:
        return []
    raw = json.loads(matches[0].read_text())
    stocks: list[dict[str, Any]] = raw.get("stocks", [])
    return [str(item.get("symbol", "")).strip().upper() for item in stocks if str(item.get("symbol", "")).strip()]


def screen_sector(
    sector: dict[str, Any],
    fetcher: VnDataFetcher,
    calls_used: int,
    max_calls: int,
) -> tuple[list[dict[str, Any]], int, int, bool]:
    sector_id = int(sector["id"])
    sector_name = str(sector["name"])
    symbols = _load_sector_symbols(sector_id)
    scanned = 0
    candidates: list[dict[str, Any]] = []
    hit_rate_limit = False

    for symbol in symbols[:MAX_STOCKS_PER_SECTOR]:
        if calls_used >= max_calls:
            break
        scanned += 1
        calls_used += 1
        try:
            raw = fetcher.get_ohlcv(symbol=symbol, days=365)
            if raw.empty:
                continue
            enriched = add_core_indicators(raw)
            if not is_candidate(enriched):
                continue
            latest = enriched.iloc[-1]
            candidates.append(
                {
                    "sector": sector_name,
                    "symbol": symbol,
                    "reason": "RSI extreme + EMA20>EMA50 crossover + volume spike",
                    "indicators": {
                        "close": float(latest.get("Close", 0.0)),
                        "rsi_14": float(latest.get("RSI_14", 0.0)),
                        "ema_20": float(latest.get("EMA_20", 0.0)),
                        "ema_50": float(latest.get("EMA_50", 0.0)),
                        "volume": float(latest.get("Volume", 0.0)),
                        "volume_sma_20": float(latest.get("VOL_SMA_20", 0.0)),
                        "macd": float(latest.get("MACD", 0.0)),
                        "macd_signal": float(latest.get("MACD_SIGNAL", 0.0)),
                        "atr_14": float(latest.get("ATR_14", 0.0)),
                    },
                }
            )
        except BaseException as exc:
            text = str(exc).lower()
            if "rate limit" in text or "process terminated" in text:
                LOGGER.warning("Rate limit reached while fetching %s in %s", symbol, sector_name)
                hit_rate_limit = True
                break
            LOGGER.debug("Skipping %s in %s: %s", symbol, sector_name, exc)

    return candidates, scanned, calls_used, hit_rate_limit


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    LOGGER.info("Starting VN weekly sector screen")
    fetcher = VnDataFetcher()
    sectors = load_vn_sectors()
    if not sectors:
        LOGGER.error("No VN sectors found; aborting")
        return

    all_candidates: list[dict[str, Any]] = []
    sector_results: list[dict[str, Any]] = []
    calls_used = 0
    hit_rate_limit = False

    for idx, sector in enumerate(sectors, start=1):
        if calls_used >= MAX_HISTORY_CALLS or hit_rate_limit:
            break
        LOGGER.info("[%d/%d] %s (id=%s)", idx, len(sectors), sector["name"], sector["id"])
        candidates, scanned, calls_used, sector_rate_limited = screen_sector(
            sector,
            fetcher,
            calls_used,
            MAX_HISTORY_CALLS,
        )
        if sector_rate_limited:
            hit_rate_limit = True
        all_candidates.extend(candidates)
        sector_results.append(
            {
                "sector_name": sector["name"],
                "stocks_scanned": scanned,
                "candidates_found": len(candidates),
            }
        )

    LOGGER.info(
        "Total VN candidates: %d across %d processed sectors (history calls: %d/%d)",
        len(all_candidates),
        len(sector_results),
        calls_used,
        MAX_HISTORY_CALLS,
    )

    ai_agent = TradingAnalysisAgent(
        project_endpoint=settings.project_endpoint,
        agent_name=settings.agent_name,
    )
    ai_summary = ai_agent.summarize_screening_results(all_candidates)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_table = build_summary_table(sector_results)
    total_candidates = len(all_candidates)

    if total_candidates > 0:
        candidates_table = build_candidates_table(all_candidates)
        msg = (
            f"📈 *Weekly Sector Screen (VN)* — {date_str}\n\n"
            f"*Sector Summary*\n"
            f"{summary_table}\n\n"
            f"*Candidates ({total_candidates})*\n"
            f"{candidates_table}\n\n"
            f"*AI Analysis*\n{ai_summary}"
        )
    else:
        msg = (
            f"📈 *Weekly Sector Screen (VN)* — {date_str}\n\n"
            f"*Sector Summary*\n"
            f"{summary_table}\n\n"
            f"No candidates matched screening criteria this week."
        )

    if calls_used >= MAX_HISTORY_CALLS or hit_rate_limit:
        msg = (
            f"{msg}\n\n"
            f"_Note: scan truncated due VN API limits ({calls_used}/{MAX_HISTORY_CALLS} history calls)._"
        )

    send_markdown_message(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        text=msg,
        message_thread_id=settings.telegram_message_thread_id,
    )
    LOGGER.info("VN weekly screen job complete")


if __name__ == "__main__":
    main()
