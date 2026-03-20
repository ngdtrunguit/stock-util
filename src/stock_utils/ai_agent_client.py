"""Azure AI Foundry wrapper for screening-result summarization."""

from __future__ import annotations

import json
import logging
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

    Returns a deduplicated, sorted list of ticker symbol strings suitable
    for passing to Azure AI Foundry agents (e.g. finrobot stock analyst).
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
    """Summarizes screener candidates via Azure AI Foundry when configured."""

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

        try:
            client = AIProjectClient(endpoint=self.project_endpoint, credential=DefaultAzureCredential())
            payload = {
                "task": "Summarize stock screening candidates for a trading update",
                "candidates": candidates,
                "format": "markdown",
            }

            agents_client: Any = client.agents
            complete_fn: Callable[..., Any] | None = getattr(agents_client, "complete", None)
            if complete_fn is None:
                LOGGER.warning("Azure agents.complete API not available; using fallback summary")
                return self._fallback_summary(candidates)

            response = complete_fn(
                agent_id=self.agent_name,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            text = getattr(response, "content", None)
            if isinstance(text, str) and text.strip():
                return text
            return self._fallback_summary(candidates)
        except Exception as exc:
            LOGGER.warning("Azure summarization failed: %s", exc)
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

        try:
            client = AIProjectClient(
                endpoint=self.project_endpoint,
                credential=DefaultAzureCredential(),
            )
            payload: dict[str, Any] = {
                "task": "Analyze the following stock tickers",
                "tickers": tickers,
                "strategy": strategy,
            }

            agents_client: Any = client.agents
            complete_fn: Callable[..., Any] | None = getattr(
                agents_client, "complete", None
            )
            if complete_fn is None:
                LOGGER.warning(
                    "Azure agents.complete API not available; skipping ticker analysis"
                )
                return None

            response = complete_fn(
                agent_id=self.agent_name,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            text = getattr(response, "content", None)
            if isinstance(text, str) and text.strip():
                return text
            return None
        except Exception as exc:
            LOGGER.warning("Azure ticker analysis failed: %s", exc)
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
