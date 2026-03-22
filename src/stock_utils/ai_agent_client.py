"""Azure AI Foundry wrapper for screening-result summarization and ranking."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Callable

try:
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential
except Exception:
    AIProjectClient = None
    DefaultAzureCredential = None

LOGGER = logging.getLogger(__name__)


def extract_tickers(candidates: list[dict[str, Any]]) -> list[str]:
    """Extract unique ticker symbols from a list of screening candidates.

    Returns ticker symbol strings in stable first-seen order without duplicates,
    suitable for passing to Azure AI Foundry agents (e.g. finrobot stock analyst).
    """
    seen: set[str] = set()
    tickers: list[str] = []
    for candidate in candidates:
        symbol = candidate.get("symbol", "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            tickers.append(symbol)
    return tickers


class TradingAnalysisAgent:
    """Summarizes and ranks screener candidates via Azure AI Foundry when configured."""

    def __init__(self, project_endpoint: str, agent_name: str) -> None:
        self.project_endpoint = project_endpoint
        self.agent_name = agent_name

    def summarize_screening_results(self, candidates: list[dict[str, Any]]) -> str:
        """Return markdown summary from Azure agent, with local fallback."""
        if not candidates:
            return "## Daily Stock Screen\n\nNo candidates matched today's criteria."

        if not self.project_endpoint or not self.agent_name:
            return self._fallback_summary(candidates)

        if AIProjectClient is None or DefaultAzureCredential is None:
            LOGGER.warning("Azure SDK not available; using fallback summary")
            return self._fallback_summary(candidates)

        payload = {
            "task": "Summarize stock screening candidates for a trading update",
            "candidates": candidates,
            "format": "markdown",
        }
        try:
            text = self._complete_with_payload(payload)
            if text:
                return text
        except Exception as exc:
            LOGGER.warning("Azure summarization failed: %s", exc)
            return self._fallback_summary(candidates)
        return self._fallback_summary(candidates)

    def send_tickers_for_analysis(
        self,
        tickers: list[str],
        strategy: str = "",
    ) -> str | None:
        """Send a clean list of ticker symbols to Azure AI Foundry for analysis.

        Unlike :meth:`summarize_screening_results`, this sends *only* the ticker
        names — no indicator data — so the agent (e.g. finrobot stock analyst)
        can fetch and process its own data for each symbol.

        Returns the agent response text, or ``None`` on failure or when Azure
        AI Foundry is not configured.
        """
        if not tickers:
            return None

        if not self.project_endpoint or not self.agent_name:
            LOGGER.debug("Azure AI Foundry not configured; skipping ticker analysis")
            return None

        if AIProjectClient is None or DefaultAzureCredential is None:
            LOGGER.warning("Azure SDK not available; skipping ticker analysis")
            return None

        payload: dict[str, Any] = {
            "task": "Analyze the following stock tickers",
            "tickers": tickers,
            "strategy": strategy,
        }
        try:
            return self._complete_with_payload(payload)
        except Exception as exc:
            LOGGER.warning("Azure ticker analysis failed: %s", exc)
            return None

    def select_top_tickers_for_telegram(
        self,
        tickers: list[str],
        max_tickers: int = 10,
    ) -> str | None:
        """Return a Telegram-friendly markdown ranking from a ticker-only list."""
        if not tickers:
            return None

        if not self.project_endpoint or not self.agent_name:
            LOGGER.debug("Azure AI Foundry not configured; skipping top-ticker selection")
            return None

        if AIProjectClient is None or DefaultAzureCredential is None:
            LOGGER.warning("Azure SDK not available; skipping top-ticker selection")
            return None

        limit = min(max_tickers, len(tickers))
        payload: dict[str, Any] = {
            "task": "Select the best stock tickers from a combined post-filter daily screen list",
            "tickers": tickers,
            "max_tickers": limit,
            "format": "telegram_markdown",
            "instructions": [
                f"Choose the top {limit} best tickers from ONLY the provided list.",
                "If fewer than the requested number of tickers are provided, return all of them.",
                "Use concise reasons focused on trend, momentum, relative strength, or setup quality.",
                "Format the response as Telegram-friendly Markdown using a short heading and a numbered list.",
                "Format each line like: 1. *TICKER* - concise reason",
                "Do not include tables, JSON, code fences, or extra sections.",
            ],
        }
        try:
            return self._complete_with_payload(payload)
        except Exception as exc:
            LOGGER.warning("Azure top-ticker selection failed: %s", exc)
            return None

    def _complete_with_payload(self, payload: dict[str, Any]) -> str | None:
        """Send a JSON payload to the configured Azure agent and return text."""
        client = AIProjectClient(
            endpoint=self.project_endpoint,
            credential=DefaultAzureCredential(),
        )
        agents_client: Any = client.agents
        complete_fn: Callable[..., Any] | None = getattr(agents_client, "complete", None)
        if complete_fn is None:
            LOGGER.warning("Azure agents.complete API not available")
            return None

        response = complete_fn(
            agent_id=self.agent_name,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
        text = self._extract_text(response)
        if text:
            return text
        LOGGER.info(
            "Azure agent returned no extractable text for task=%s; response_type=%s",
            payload.get("task", ""),
            type(response).__name__,
        )
        return None

    @classmethod
    def _extract_text(cls, value: Any) -> str | None:
        """Best-effort extraction of text from Azure SDK response shapes."""
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        if isinstance(value, bytes):
            text = value.decode(errors="ignore").strip()
            return text or None
        if isinstance(value, Mapping):
            for key in (
                "content",
                "text",
                "value",
                "message",
                "output_text",
                "response",
                "result",
                "data",
            ):
                text = cls._extract_text(value.get(key))
                if text:
                    return text
            return None
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            parts = [part for item in value if (part := cls._extract_text(item))]
            if parts:
                return "\n".join(parts)
            return None

        for attr in ("content", "text", "value", "message", "output_text", "response", "result"):
            if hasattr(value, attr):
                text = cls._extract_text(getattr(value, attr))
                if text:
                    return text

        as_dict = getattr(value, "as_dict", None)
        if callable(as_dict):
            try:
                text = cls._extract_text(as_dict())
                if text:
                    return text
            except Exception:
                pass

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                text = cls._extract_text(model_dump())
                if text:
                    return text
            except Exception:
                pass

        return None

    @staticmethod
    def _fallback_summary(candidates: list[dict[str, Any]]) -> str:
        def _fmt_number(value: Any) -> str:
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return "n/a"

        lines = ["## Daily Stock Screen", "", f"Candidates found: {len(candidates)}", ""]
        for item in candidates:
            symbol = item.get("symbol", "UNKNOWN")
            indicators = item.get("indicators", {})
            ma_200 = indicators.get("ma_200")
            ma_200_text = f", MA200={_fmt_number(ma_200)}" if ma_200 is not None else ""
            lines.append(
                f"- **{symbol}**: RSI={_fmt_number(indicators.get('rsi_14'))}, "
                f"Close={_fmt_number(indicators.get('close'))}"
                f"{ma_200_text}, "
                f"Reason: {item.get('reason', 'n/a')}"
            )
        return "\n".join(lines)
