"""Monthly VN sector sync: persist vnstock sectors to VN-only files."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

from stock_utils.config import Settings
from stock_utils.telegram_notifier import send_markdown_message
from stock_utils.vn_data_fetcher import VnDataFetcher
from stock_utils.vn_paths import VN_SECTORS_DIR, VN_SECTORS_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _build_table(sectors: list[dict]) -> str:
    header = "| # | Sector | Symbols | ID |"
    sep = "|---|--------|---------|----|"
    lines = [header, sep]
    for idx, sec in enumerate(sectors, start=1):
        lines.append(f"| {idx} | {sec['name']} | {len(sec['symbols'])} | {sec['id']} |")
    return "\n".join(lines)


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    LOGGER.info("Starting VN monthly sector sync")
    fetcher = VnDataFetcher()
    sectors = fetcher.get_sector_symbols()

    now_iso = datetime.now(timezone.utc).isoformat()
    VN_SECTORS_DIR.mkdir(parents=True, exist_ok=True)
    VN_SECTORS_FILE.parent.mkdir(parents=True, exist_ok=True)

    for sec in sectors:
        file_path = VN_SECTORS_DIR / f"{sec['id']}-{_slug(sec['name'])}.json"
        payload = {
            "updated_at": now_iso,
            "sector_id": sec["id"],
            "sector_name": sec["name"],
            "stock_count": len(sec["symbols"]),
            "stocks": [{"symbol": symbol} for symbol in sec["symbols"]],
        }
        file_path.write_text(json.dumps(payload, indent=2))

    snapshot = {
        "updated_at": now_iso,
        "market": "VN",
        "source": "vnstock",
        "sector_count": len(sectors),
        "sectors": [
            {
                "id": sec["id"],
                "name": sec["name"],
                "stock_count": len(sec["symbols"]),
            }
            for sec in sectors
        ],
    }
    VN_SECTORS_FILE.write_text(json.dumps(snapshot, indent=2))
    LOGGER.info("Saved %d VN sectors to %s", len(sectors), VN_SECTORS_FILE)

    msg = (
        f"📊 *Monthly Sector Sync (VN)* — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
        f"Sectors tracked: {len(sectors)}\n\n"
        f"{_build_table(sectors[:40])}"
    )
    send_markdown_message(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        text=msg,
        message_thread_id=settings.telegram_message_thread_id,
    )
    LOGGER.info("VN monthly sector sync complete")


if __name__ == "__main__":
    main()
