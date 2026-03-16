"""Environment-driven configuration for stock screening jobs."""

from __future__ import annotations

from dataclasses import dataclass
import os

from stock_utils.paths import DEFAULT_WATCHLIST_FILE, resolve_repo_path


def _optional_int_from_env(name: str) -> int | None:
    """Return an integer env var value when present, otherwise None."""
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from environment variables."""

    project_endpoint: str
    agent_name: str
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_message_thread_id: int | None
    watchlist_file: str = str(DEFAULT_WATCHLIST_FILE)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            project_endpoint=os.getenv("PROJECT_ENDPOINT", ""),
            agent_name=os.getenv("AGENT_NAME", ""),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            telegram_message_thread_id=_optional_int_from_env("TELEGRAM_MESSAGE_THREAD_ID"),
            watchlist_file=str(
                resolve_repo_path(os.getenv("WATCHLIST_FILE", str(DEFAULT_WATCHLIST_FILE)))
            ),
        )
