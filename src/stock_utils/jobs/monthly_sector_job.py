"""Monthly job: scrape all Webull sectors and persist to data/sectors.json."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv

from stock_utils.sector_scraper import SectorScraper
from stock_utils.config import Settings
from stock_utils.paths import SECTORS_FILE
from stock_utils.telegram_notifier import send_markdown_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)

def build_table(sectors: list[dict]) -> str:
    """Render sector list as Markdown table."""
    header = "| # | Sector | 1D % | 5D % | 1M % | 3M % | ID |"
    sep    = "|---|--------|------|------|------|------|----|"
    lines  = [header, sep]

    # build a lookup: sector_id -> {period: pct}
    id_map: dict[int, dict] = {}
    for s in sectors:
        id_map.setdefault(s["id"], {})[s["_period"]] = s.get("change_pct")

    # keep original 1d order for numbering
    ids_1d = [s["id"] for s in sectors if s.get("_period") == "1d"]

    for rank, sid in enumerate(ids_1d, start=1):
        name = next((s["name"] for s in sectors if s["id"] == sid), str(sid))
        pcts = id_map.get(sid, {})

        def fmt(v: float | None) -> str:
            if v is None:
                return "n/a"
            sign = "+" if v >= 0 else ""
            return f"{sign}{v * 100:.2f}%"

        lines.append(
            f"| {rank} | {name} | {fmt(pcts.get('1d'))} | {fmt(pcts.get('5d'))} "
            f"| {fmt(pcts.get('1m'))} | {fmt(pcts.get('3m'))} | {sid} |"
        )

    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    LOGGER.info("Starting monthly sector sync")
    scraper = SectorScraper()

    all_entries: list[dict] = []
    for period in ["1d", "5d", "1m", "3m"]:
        secs = scraper.get_all_sectors(period=period)
        for s in secs:
            s["_period"] = period
        all_entries.extend(secs)
        LOGGER.info("Period %s: %d sectors", period, len(secs))

    now_iso = datetime.now(timezone.utc).isoformat()

    # persist master sectors index
    SECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "updated_at": now_iso,
        "sectors": all_entries,
    }
    SECTORS_FILE.write_text(json.dumps(snapshot, indent=2))
    LOGGER.info("Saved %d sector entries to %s", len(all_entries), SECTORS_FILE)

    # unique sectors by id for table — only 1d entries carry authoritative names
    sectors_1d = [s for s in all_entries if s.get("_period") == "1d"]
    table = build_table(all_entries)

    msg = (
        f"📊 *Monthly Sector Sync* — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"Sectors tracked: {len(sectors_1d)}\n\n"
        f"{table}"
    )

    send_markdown_message(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        text=msg,
        message_thread_id=settings.telegram_message_thread_id,
    )
    LOGGER.info("Monthly sector sync complete")


if __name__ == "__main__":
    main()
