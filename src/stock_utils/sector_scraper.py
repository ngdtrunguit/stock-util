"""Webull public-sector scraper using embedded server-rendered state."""

from __future__ import annotations

import logging
import json
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)


class SectorScraper:
    """Scrape Webull hot sector pages without authentication."""

    HOT_SECTORS_URL = "https://www.webull.com/quote/us/hot-sector/{period}?hl=en"
    SUPPORTED_PERIODS = {"1d", "5d", "1m", "3m"}

    def get_all_sectors(self, period: str = "1d") -> list[dict[str, Any]]:
        """Return all sectors available from Webull hot-sector page.

        Args:
            period: One of 1d, 5d, 1m, 3m.

        Returns:
            List of sector dictionaries sorted by current change percentage.
        """
        if period not in self.SUPPORTED_PERIODS:
            raise ValueError(f"Unsupported period '{period}'. Use one of: {sorted(self.SUPPORTED_PERIODS)}")

        url = self.HOT_SECTORS_URL.format(period=period)
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.warning("Failed to fetch Webull sectors page: %s", exc)
            return []

        raw_state = self._extract_init_state(response.text)
        if not raw_state:
            LOGGER.warning("Could not parse Webull init state from page")
            return []

        hot_sector = raw_state.get("quoteHomeData", {}).get("hotSector", {})
        data = hot_sector.get("data", [])
        if not isinstance(data, list):
            LOGGER.warning("Unexpected Webull data payload shape")
            return []

        sectors: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            name = item.get("name")
            sector_id = item.get("id")
            if not name or sector_id is None:
                continue

            change_pct = self._to_float(item.get("changeRatio"))
            sectors.append(
                {
                    "id": int(sector_id),
                    "name": str(name),
                    "change_pct": change_pct,
                    "price": self._to_float(item.get("price")),
                    "market_value": self._to_float(item.get("marketValue")),
                    "volume": self._to_float(item.get("volume")),
                    "turnover_rate": self._to_float(item.get("turnoverRate")),
                    "symbol": item.get("symbol"),
                    "url": f"https://www.webull.com/quote/us/hot-sector/{period}/{sector_id}",
                }
            )

        sectors.sort(key=lambda x: x.get("change_pct") or 0.0, reverse=True)
        return sectors

    def get_top_sectors(self, limit: int = 5, period: str = "1d") -> list[dict[str, Any]]:
        """Return top N sectors for a given ranking period."""
        sectors = self.get_all_sectors(period=period)
        return sectors[:limit]

    def get_sector_stocks(self, sector_id: int, period: str = "1d") -> list[dict[str, Any]]:
        """Return all stocks listed inside a sector detail page.

        Args:
            sector_id: Numeric Webull sector ID (from ``get_all_sectors`` result).
            period: Ranking period — one of 1d, 5d, 1m, 3m.

        Returns:
            List of stock dicts with symbol, name, exchange, change, changeRatio.
        """
        if period not in self.SUPPORTED_PERIODS:
            raise ValueError(f"Unsupported period '{period}'. Use one of: {sorted(self.SUPPORTED_PERIODS)}")

        url = f"https://www.webull.com/quote/us/hot-sector/{period}/{sector_id}?hl=en"
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.warning("Failed to fetch sector detail %s: %s", sector_id, exc)
            return []

        raw_state = self._extract_init_state(response.text)
        if not raw_state:
            LOGGER.warning("Could not parse init state for sector %s", sector_id)
            return []

        detail = raw_state.get("quoteHomeData", {}).get("sectorsDetail", {})
        raw_stocks: list[Any] = detail.get("data", [])

        stocks: list[dict[str, Any]] = []
        for item in raw_stocks:
            if not isinstance(item, dict):
                continue
            values = item.get("values", {})
            ticker = item.get("ticker", {})
            symbol = ticker.get("disSymbol") or ticker.get("symbol")
            if not symbol:
                continue
            stocks.append(
                {
                    "symbol": str(symbol),
                    "name": ticker.get("name", ""),
                    "exchange": ticker.get("disExchangeCode", ""),
                    "change": self._to_float(values.get("change")),
                    "change_pct": self._to_float(values.get("changeRatio")),
                }
            )

        stocks.sort(key=lambda x: x.get("change_pct") or 0.0, reverse=True)
        return stocks

    @staticmethod
    def _extract_init_state(page_html: str) -> dict[str, Any] | None:
        """Extract window.__initState__ JSON object from page HTML."""
        marker = "window.__initState__="
        idx = page_html.find(marker)
        if idx == -1:
            return None

        payload = page_html[idx + len(marker) :]
        stripped = payload.lstrip()
        if not stripped.startswith("{"):
            return None

        decoder = json.JSONDecoder()
        try:
            state, _ = decoder.raw_decode(stripped)
        except json.JSONDecodeError:
            return None

        if isinstance(state, dict):
            return state
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
