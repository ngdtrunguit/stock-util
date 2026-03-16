"""Telegram notification client for markdown summaries."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

LOGGER = logging.getLogger(__name__)


def send_markdown_message(
    bot_token: str,
    chat_id: str,
    text: str,
    message_thread_id: int | None = None,
) -> None:
    """Send a markdown-formatted message to Telegram."""
    if not bot_token or not chat_id:
        LOGGER.warning("Telegram credentials missing; notification skipped")
        return

    async def _send() -> None:
        bot = Bot(token=bot_token)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                message_thread_id=message_thread_id,
            )
        finally:
            await bot.session.close()

    asyncio.run(_send())
