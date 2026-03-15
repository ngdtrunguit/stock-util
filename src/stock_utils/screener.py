"""Orchestration logic to screen symbols against indicator criteria."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from .data_fetcher import DataFetcher
from .indicators import add_core_indicators, is_candidate, is_price_above_ma200

LOGGER = logging.getLogger(__name__)


@dataclass
class Screener:
    """Runs screening logic for a universe of symbols."""

    data_fetcher: DataFetcher
    watchlist_file: str = "data/watchlist.txt"
    period: str = "6mo"
    interval: str = "1d"
    strategy: str = "default"

    def _matches_strategy(self, enriched: Any) -> bool:
        if self.strategy == "price_above_ma200":
            return is_price_above_ma200(enriched)
        return is_candidate(enriched)

    def _reason_for_strategy(self) -> str:
        if self.strategy == "price_above_ma200":
            return "Daily close is above the 200-day moving average"
        return "RSI extreme + bullish EMA20/EMA50 crossover + volume confirmation"

    def run_screen(self) -> list[dict[str, Any]]:
        """Run the configured screening process and return candidate summaries."""
        symbols = self.data_fetcher.get_watchlist(self.watchlist_file)
        results: list[dict[str, Any]] = []

        LOGGER.info("Screening %d symbols", len(symbols))
        for idx, symbol in enumerate(symbols, start=1):
            LOGGER.info("[%d/%d] Screening %s", idx, len(symbols), symbol)
            try:
                raw = self.data_fetcher.get_ohlcv(symbol=symbol, period=self.period, interval=self.interval)
                enriched = add_core_indicators(raw)

                if not self._matches_strategy(enriched):
                    continue

                latest = enriched.iloc[-1]
                results.append(
                    {
                        "symbol": symbol,
                        "reason": self._reason_for_strategy(),
                        "indicators": {
                            "close": float(latest.get("Close", 0.0)),
                            "ma_200": float(latest.get("MA_200", 0.0)),
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
                LOGGER.warning("Skipping %s due to error: %s", symbol, exc)

        LOGGER.info("Screening complete. Candidates found: %d", len(results))
        return results
