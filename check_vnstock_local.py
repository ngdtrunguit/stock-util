"""Local smoke test for VN market data via vnstock (separate from US jobs).

This script does not modify existing job logic. It only verifies that vnstock
can return:
1) A VN symbol universe
2) Historical OHLCV data for sample VN tickers

Usage:
    python check_vnstock_local.py
    python check_vnstock_local.py --symbols VCB FPT HPG
    python check_vnstock_local.py --source VCI
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata
from typing import Iterable

from vnstock import Listing, Vnstock


EXPECTED_VERSION = "3.5.0"
DEFAULT_SYMBOLS = ("VCB", "FPT", "HPG")
DEFAULT_SOURCE = "VCI"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test vnstock connectivity and payloads.")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(DEFAULT_SYMBOLS),
        help="VN tickers to test history retrieval.",
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help="vnstock source provider for quote history (for example: VCI).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=45,
        help="Lookback days for history fetch window.",
    )
    return parser.parse_args()


def _print_result(result: CheckResult) -> None:
    mark = "PASS" if result.ok else "FAIL"
    print(f"[{mark}] {result.name}: {result.detail}")


def check_vnstock_version() -> CheckResult:
    try:
        installed = metadata.version("vnstock")
    except metadata.PackageNotFoundError:
        return CheckResult("vnstock installation", False, "Package not installed in current environment")

    if installed == EXPECTED_VERSION:
        return CheckResult("vnstock version", True, f"Installed {installed}")

    return CheckResult(
        "vnstock version",
        False,
        f"Installed {installed}, expected {EXPECTED_VERSION}",
    )


def check_symbol_universe() -> CheckResult:
    try:
        df = Listing().all_symbols()
    except Exception as exc:  # pragma: no cover - network/API failures are runtime-dependent
        return CheckResult("VN symbol universe", False, f"all_symbols() failed: {type(exc).__name__}: {exc}")

    rows = int(getattr(df, "shape", (0, 0))[0])
    columns = [str(c) for c in getattr(df, "columns", [])]
    if rows <= 0:
        return CheckResult("VN symbol universe", False, "No symbols returned")

    return CheckResult(
        "VN symbol universe",
        True,
        f"Rows={rows}, columns={columns[:5]}",
    )


def _history_window(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def check_history(symbol: str, source: str, days: int) -> CheckResult:
    start, end = _history_window(days)

    try:
        quote = Vnstock().stock(symbol=symbol, source=source).quote
        df = quote.history(start=start, end=end, interval="1D")
    except Exception as exc:  # pragma: no cover - network/API failures are runtime-dependent
        return CheckResult(
            f"History {symbol}",
            False,
            f"history() failed via source={source}: {type(exc).__name__}: {exc}",
        )

    rows = int(getattr(df, "shape", (0, 0))[0])
    if rows <= 0:
        return CheckResult(f"History {symbol}", False, f"No rows returned for {start} -> {end}")

    tail = df.tail(1).to_dict("records")[0] if hasattr(df, "tail") else {}
    close = tail.get("close", "n/a")
    volume = tail.get("volume", "n/a")
    return CheckResult(
        f"History {symbol}",
        True,
        f"Rows={rows}, latest close={close}, latest volume={volume}",
    )


def run(symbols: Iterable[str], source: str, days: int) -> int:
    results: list[CheckResult] = []
    results.append(check_vnstock_version())
    results.append(check_symbol_universe())

    normalized_symbols = [s.strip().upper() for s in symbols if s.strip()]
    for symbol in normalized_symbols:
        results.append(check_history(symbol=symbol, source=source, days=days))

    print("vnstock local smoke test")
    print(f"Source provider: {source}")
    print(f"Symbols: {normalized_symbols}")
    print()

    for result in results:
        _print_result(result)

    failed = [r for r in results if not r.ok]
    print()
    print(f"Summary: {len(results) - len(failed)}/{len(results)} checks passed")

    if failed:
        print("Result: FAILED")
        return 1

    print("Result: PASSED")
    return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(run(symbols=args.symbols, source=args.source, days=args.days))


if __name__ == "__main__":
    main()
