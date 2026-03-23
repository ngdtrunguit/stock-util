"""Local VN pipeline checker for VN monthly sector sync + VN weekly screen.

This script is intended to validate whether the current jobs are truly VN-ready
before relying on the GitHub Actions workflow.

Usage:
    python check_vn_jobs_local.py
    python check_vn_jobs_local.py --run-jobs
    python check_vn_jobs_local.py --run-jobs --allow-telegram
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from stock_utils.jobs import monthly_sector_vn_job, weekly_screen_vn_job  # noqa: E402
from stock_utils.vn_paths import VN_SECTORS_DIR, VN_SECTORS_FILE  # noqa: E402
from stock_utils.vn_data_fetcher import VnDataFetcher  # noqa: E402


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check VN readiness for stock jobs.")
    parser.add_argument(
        "--run-jobs",
        action="store_true",
        help="Run monthly_sector_vn_job then weekly_screen_vn_job after preflight checks.",
    )
    parser.add_argument(
        "--allow-telegram",
        action="store_true",
        help="Allow Telegram notifications during local run (default disables them).",
    )
    return parser.parse_args()


def _print_result(result: CheckResult) -> None:
    mark = "PASS" if result.ok else "FAIL"
    print(f"[{mark}] {result.name}: {result.detail}")


def _snapshot_file(path: Path) -> tuple[bool, float]:
    if not path.exists():
        return (False, 0.0)
    return (True, path.stat().st_mtime)


def check_vnstock_sector_fetch() -> CheckResult:
    try:
        sectors = VnDataFetcher().get_sector_symbols()
    except Exception as exc:
        return CheckResult(
            name="vnstock sectors",
            ok=False,
            detail=f"Failed to fetch VN sectors: {type(exc).__name__}: {exc}",
        )
    if sectors:
        return CheckResult(
            name="vnstock sectors",
            ok=True,
            detail=f"Fetched {len(sectors)} VN sectors",
        )
    return CheckResult(
        name="vnstock sectors",
        ok=False,
        detail="No VN sectors returned",
    )


def probe_live_history_payload() -> CheckResult:
    try:
        df = VnDataFetcher().get_ohlcv("VCB", days=30)
    except Exception as exc:
        return CheckResult(
            name="vnstock history",
            ok=False,
            detail=f"History fetch failed: {type(exc).__name__}: {exc}",
        )
    if not df.empty:
        return CheckResult(
            name="vnstock history",
            ok=True,
            detail=f"Fetched {len(df)} rows for VCB",
        )
    return CheckResult(
        name="vnstock history",
        ok=False,
        detail="No rows returned for VCB",
    )


def run_monthly_and_verify() -> list[CheckResult]:
    results: list[CheckResult] = []
    existed_before, mtime_before = _snapshot_file(VN_SECTORS_FILE)

    monthly_sector_vn_job.main()

    if not VN_SECTORS_FILE.exists():
        results.append(
            CheckResult("monthly_sector_vn_job output", False, f"Missing file: {VN_SECTORS_FILE}")
        )
        return results

    raw = json.loads(VN_SECTORS_FILE.read_text())
    sectors = raw.get("sectors", []) if isinstance(raw, dict) else []
    has_sector_count = len(sectors) > 0
    results.append(
        CheckResult(
            "monthly_sector_vn_job sectors",
            has_sector_count,
            f"Total VN sectors={len(sectors)}",
        )
    )

    _, mtime_after = _snapshot_file(VN_SECTORS_FILE)
    changed = (not existed_before) or (mtime_after > mtime_before)
    results.append(
        CheckResult(
            "monthly_sector_vn_job refresh",
            changed,
            "sectors-vn.json updated" if changed else "sectors-vn.json timestamp unchanged",
        )
    )

    return results


def run_weekly_and_verify() -> list[CheckResult]:
    results: list[CheckResult] = []
    existing_files_before = {p.name: p.stat().st_mtime for p in VN_SECTORS_DIR.glob("*.json")}

    weekly_screen_vn_job.main()

    files_after = list(VN_SECTORS_DIR.glob("*.json"))
    if not files_after:
        results.append(
            CheckResult(
                "weekly_screen_vn_job sector files",
                False,
                f"No per-sector files found in {VN_SECTORS_DIR}",
            )
        )
        return results

    refreshed = len(files_after) >= len(existing_files_before)

    results.append(
        CheckResult(
            "weekly_screen_vn_job sector refresh",
            refreshed,
            f"VN sector files present: {len(files_after)}",
        )
    )

    return results


def disable_telegram_env() -> dict[str, str | None]:
    keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_MESSAGE_THREAD_ID"]
    backup = {k: os.environ.get(k) for k in keys}
    for key in keys:
        os.environ.pop(key, None)
    return backup


def restore_env(backup: dict[str, str | None]) -> None:
    for key, value in backup.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def main() -> None:
    args = parse_args()

    print("VN Local Check: monthly_sector_vn_job + weekly_screen_vn_job")
    print(f"Repository: {REPO_ROOT}")

    results: list[CheckResult] = []
    results.append(check_vnstock_sector_fetch())
    results.append(probe_live_history_payload())

    for result in results:
        _print_result(result)

    if not args.run_jobs:
        failed = [r for r in results if not r.ok]
        print("\nTip: run with --run-jobs to execute full local flow.")
        raise SystemExit(1 if failed else 0)

    env_backup: dict[str, str | None] = {}
    if not args.allow_telegram:
        env_backup = disable_telegram_env()
        print("\nTelegram env disabled for local safety.")

    history_calls_before = os.environ.get("VN_WEEKLY_MAX_HISTORY_CALLS")
    sector_offset_before = os.environ.get("VN_WEEKLY_SECTOR_OFFSET")
    os.environ["VN_WEEKLY_MAX_HISTORY_CALLS"] = "8"
    os.environ["VN_WEEKLY_SECTOR_OFFSET"] = "3"

    try:
        print("\nRunning monthly_sector_vn_job...")
        monthly_results = run_monthly_and_verify()
        for result in monthly_results:
            _print_result(result)

        print("\nRunning weekly_screen_vn_job...")
        weekly_results = run_weekly_and_verify()
        for result in weekly_results:
            _print_result(result)

        results.extend(monthly_results)
        results.extend(weekly_results)
    finally:
        if history_calls_before is None:
            os.environ.pop("VN_WEEKLY_MAX_HISTORY_CALLS", None)
        else:
            os.environ["VN_WEEKLY_MAX_HISTORY_CALLS"] = history_calls_before
        if sector_offset_before is None:
            os.environ.pop("VN_WEEKLY_SECTOR_OFFSET", None)
        else:
            os.environ["VN_WEEKLY_SECTOR_OFFSET"] = sector_offset_before
        if env_backup:
            restore_env(env_backup)

    failed = [r for r in results if not r.ok]
    print("\nSummary")
    print(f"Total checks: {len(results)}")
    print(f"Failed checks: {len(failed)}")

    if failed:
        print("\nVN readiness is NOT fully verified. See FAIL checks above.")
        raise SystemExit(1)

    print("\nVN readiness checks passed.")


if __name__ == "__main__":
    main()
