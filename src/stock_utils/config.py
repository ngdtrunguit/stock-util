"""Environment-driven configuration for stock screening jobs."""

from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    project_endpoint: str
    agent_name: str
    telegram_bot_token: str
    telegram_chat_id: str
    watchlist_file: str = "data/watchlist.txt"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            project_endpoint=os.getenv("PROJECT_ENDPOINT", ""),
            agent_name=os.getenv("AGENT_NAME", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            watchlist_file=os.getenv("WATCHLIST_FILE", "data/watchlist.txt"),
        )
