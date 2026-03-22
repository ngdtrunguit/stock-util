"""VN daily screen job clone using VN sector universe + Yahoo price analysis.

This entrypoint intentionally reuses the existing daily screen orchestration to
keep behavior aligned with the US pipeline while switching the data universe.
"""

from __future__ import annotations

from stock_utils.jobs import daily_screen_job as base_daily
from stock_utils.paths import DATA_DIR
from stock_utils.vn_data_fetcher import VnDataFetcher
from stock_utils.vn_paths import VN_SECTORS_DIR


class _VnDailyFetcher(VnDataFetcher):
    """Adapter exposing the same fetcher methods used by daily_screen_job."""

    def get_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d"):
        return self.get_ohlcv_for_daily_job(symbol=symbol, period=period, interval=interval)


def main() -> None:
    # Switch the sector universe from US sectors to VN sectors.
    base_daily.SECTORS_DIR = VN_SECTORS_DIR

    # Keep VN artifacts separated from US output directory.
    base_daily.OUTPUT_DIR = DATA_DIR / "output-vn"

    # Use VN fetcher that discovers sectors via vnstock and analyzes prices via Yahoo (.VN).
    base_daily.DataFetcher = _VnDailyFetcher

    base_daily.main()


if __name__ == "__main__":
    main()
