"""Weekly job: screen top stocks across all saved sectors, report in a table."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from stock_utils.ai_agent_client import TradingAnalysisAgent
from stock_utils.config import Settings
from stock_utils.data_fetcher import DataFetcher
from stock_utils.indicators import add_core_indicators, is_candidate
from stock_utils.paths import SECTORS_DIR, SECTORS_FILE
from stock_utils.sector_scraper import SectorScraper
from stock_utils.telegram_notifier import send_markdown_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)

MAX_STOCKS_PER_SECTOR = 50   # Webull returns up to 50 per sector page
MAX_SECTORS = 30              # all available sectors


def _sector_slug(name: str) -> str:
    """Convert sector name to a safe filename slug."""
    import re
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"


def _fmt_float(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def build_candidates_table(candidates: list[dict[str, Any]]) -> str:
    """Render candidate rows as a Markdown table."""
    header = "| Sector | Symbol | Close | RSI | EMA20 | EMA50 | Vol/SMA | MACD | ATR |"
    sep    = "|--------|--------|-------|-----|-------|-------|---------|------|-----|"
    lines  = [header, sep]
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
    """Summarise how many candidates per sector."""
    header = "| Sector | Stocks Scanned | Candidates |"
    sep    = "|--------|---------------|------------|"
    lines  = [header, sep]
    for r in sector_results:
        lines.append(
            f"| {r['sector_name']} | {r['stocks_scanned']} | {r['candidates_found']} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def load_sectors() -> list[dict[str, Any]]:
    """Load persisted sector list (1d entries only) from data/sectors.json."""
    if not SECTORS_FILE.exists():
        LOGGER.warning("sectors.json not found — falling back to live scrape (1d)")
        return SectorScraper().get_all_sectors(period="1d")

    raw = json.loads(SECTORS_FILE.read_text())
    all_entries: list[dict] = raw.get("sectors", [])
    sectors_1d = [s for s in all_entries if s.get("_period") == "1d"]
    LOGGER.info(
        "Loaded %d sectors from snapshot (updated: %s)",
        len(sectors_1d),
        raw.get("updated_at", "unknown"),
    )
    return sectors_1d[:MAX_SECTORS]


def _load_sector_stocks(sector_id: int, sector_name: str) -> list[dict[str, Any]]:
    """Load pre-fetched sector stocks from data/sectors/<id>-*.json."""
    pattern = f"{sector_id}-*.json"
    matches = list(SECTORS_DIR.glob(pattern))
    if matches:
        raw = json.loads(matches[0].read_text())
        stocks: list[dict] = raw.get("stocks", [])
        LOGGER.debug("Loaded %d stocks for %s from %s", len(stocks), sector_name, matches[0].name)
        return stocks
    # fallback: live fetch if file not yet written
    LOGGER.warning("No stock file for sector %s (%s) — fetching live", sector_name, sector_id)
    return SectorScraper().get_sector_stocks(sector_id=sector_id, period="1d")


def screen_sector(
    sector: dict[str, Any],
    fetcher: DataFetcher,
) -> tuple[list[dict[str, Any]], int]:
    """Screen all stocks in a sector. Returns (candidates, total_scanned)."""
    sector_id: int = sector["id"]
    sector_name: str = sector["name"]
    stocks = _load_sector_stocks(sector_id, sector_name)
    scanned = 0
    candidates: list[dict[str, Any]] = []

    for stock in stocks[:MAX_STOCKS_PER_SECTOR]:
        symbol = stock["symbol"]
        scanned += 1
        try:
            raw = fetcher.get_ohlcv(symbol=symbol, period="6mo", interval="1d")
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
        except Exception as exc:
            LOGGER.debug("Skipping %s in %s: %s", symbol, sector_name, exc)

    return candidates, scanned


def _refresh_sector_stocks(sectors: list[dict], scraper: SectorScraper) -> None:
    """Fetch and write per-sector stock files for all sectors."""
    SECTORS_DIR.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    LOGGER.info("Refreshing stock lists for %d sectors", len(sectors))
    for sec in sectors:
        sector_id: int = sec["id"]
        sector_name: str = sec["name"]
        slug = _sector_slug(sector_name)
        outfile = SECTORS_DIR / f"{sector_id}-{slug}.json"
        try:
            stocks = scraper.get_sector_stocks(sector_id=sector_id, period="1d")
            payload = {
                "updated_at": now_iso,
                "sector_id": sector_id,
                "sector_name": sector_name,
                "stock_count": len(stocks),
                "stocks": stocks,
            }
            outfile.write_text(json.dumps(payload, indent=2))
            LOGGER.info("  [%s] %d stocks -> %s", sector_name, len(stocks), outfile.name)
        except Exception as exc:
            LOGGER.warning("  Failed to refresh stocks for %s: %s", sector_name, exc)


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    LOGGER.info("Starting weekly sector screen")
    fetcher = DataFetcher()
    scraper = SectorScraper()

    sectors = load_sectors()
    LOGGER.info("Sectors to screen: %d", len(sectors))

    # Step 1: refresh per-sector stock files before screening
    _refresh_sector_stocks(sectors, scraper)

    # Step 2: screen stocks in each sector
    all_candidates: list[dict[str, Any]] = []
    sector_results: list[dict[str, Any]] = []

    for idx, sector in enumerate(sectors, start=1):
        LOGGER.info("[%d/%d] %s (id=%s)", idx, len(sectors), sector["name"], sector["id"])
        candidates, scanned = screen_sector(sector, fetcher)
        all_candidates.extend(candidates)
        sector_results.append(
            {
                "sector_name": sector["name"],
                "stocks_scanned": scanned,
                "candidates_found": len(candidates),
            }
        )

    LOGGER.info("Total candidates: %d across %d sectors", len(all_candidates), len(sectors))

    # AI summary (optional)
    ai_agent = TradingAnalysisAgent(
        project_endpoint=settings.project_endpoint,
        agent_name=settings.agent_name,
    )
    ai_summary = ai_agent.summarize_screening_results(all_candidates)

    # Build message
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    summary_table = build_summary_table(sector_results)
    total_candidates = len(all_candidates)

    if total_candidates > 0:
        candidates_table = build_candidates_table(all_candidates)
        msg = (
            f"📈 *Weekly Sector Screen* — {date_str}\n\n"
            f"*Sector Summary*\n"
            f"{summary_table}\n\n"
            f"*Candidates ({total_candidates})*\n"
            f"{candidates_table}\n\n"
            f"*AI Analysis*\n{ai_summary}"
        )
    else:
        msg = (
            f"📈 *Weekly Sector Screen* — {date_str}\n\n"
            f"*Sector Summary*\n"
            f"{summary_table}\n\n"
            f"No candidates matched screening criteria this week."
        )

    send_markdown_message(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        text=msg,
        message_thread_id=settings.telegram_message_thread_id,
    )
    LOGGER.info("Weekly screen job complete")


if __name__ == "__main__":
    main()
