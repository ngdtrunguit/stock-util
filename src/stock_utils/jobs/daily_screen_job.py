"""Daily screening orchestration entrypoint."""

from __future__ import annotations

import html as _html
import json
import logging
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from stock_utils.ai_agent_client import TradingAnalysisAgent, extract_tickers
from stock_utils.config import Settings
from stock_utils.data_fetcher import DataFetcher
from stock_utils.database import ScreeningDatabase
from stock_utils.paths import OUTPUT_DIR, SECTORS_DIR
from stock_utils.screener import Screener
from stock_utils.telegram_notifier import send_markdown_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOGGER = logging.getLogger(__name__)

# Three screening passes in priority order — shown first to last.
_PASSES = [
    {
        "strategy": "rsi_oversold_bounce_ma200",
        "filename_prefix": "daily-candidates-rsi-oversold",
        "title": "RSI Oversold Bounce + Price Above MA200",
        "rule": "RSI 14 touched ≤ 30 within last 5 trading days & Close > MA200 on D1",
        "telegram_label": "1⃣ *RSI Oversold Bounce (last 5 days) + Price > MA200*",
    },
    {
        "strategy": "golden_cross_weekly",
        "filename_prefix": "daily-candidates-golden-cross",
        "title": "Golden Cross (MA50 × MA200 last 5 days)",
        "rule": "Close > MA50 & MA200 on D1; MA50 crossed above MA200 within last 5 trading days",
        "telegram_label": "2⃣ *Golden Cross (last 5 days)*",
    },
    {
        "strategy": "price_above_ma50_ma200",
        "filename_prefix": "daily-candidates-ma",
        "title": "Price Above MA50 & MA200",
        "rule": "Close > MA50 & Close > MA200 on D1",
        "telegram_label": "3⃣ *Price > MA50 & MA200*",
    },
]


def _load_all_sectors() -> list[dict[str, Any]]:
    """Read all per-sector JSON files and return a list of sector dicts.

    Each entry: {sector_id, sector_name, symbols: [str]}
    Falls back to an empty list when the directory does not exist yet.
    """
    if not SECTORS_DIR.exists():
        LOGGER.warning(
            "%s not found — run monthly_sector_job first to populate sector files",
            SECTORS_DIR,
        )
        return []

    sectors: list[dict[str, Any]] = []
    for path in sorted(SECTORS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
            symbols = [s["symbol"] for s in raw.get("stocks", []) if s.get("symbol")]
            if not symbols:
                continue
            sectors.append(
                {
                    "sector_id": raw["sector_id"],
                    "sector_name": raw["sector_name"],
                    "symbols": symbols,
                }
            )
        except Exception as exc:
            LOGGER.warning("Skipping sector file %s: %s", path.name, exc)

    LOGGER.info("Loaded %d sectors from %s", len(sectors), SECTORS_DIR)
    return sectors


def _fmt_float(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct_above(close: Any, ma: Any) -> float | None:
    try:
        c, m = float(close), float(ma)
        return ((c / m) - 1.0) * 100.0 if m != 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    indicators = candidate.get("indicators", {})
    close  = indicators.get("close")
    ma_200 = indicators.get("ma_200")
    ma_50  = indicators.get("ma_50")

    return {
        "symbol": candidate.get("symbol", "UNKNOWN"),
        "reason": candidate.get("reason", ""),
            "sector_name": candidate.get("sector_name", ""),
        "close": close,
        "ma_200": ma_200,
        "ma_50": ma_50,
        "rsi_14": indicators.get("rsi_14"),
        "pct_above_ma200": _pct_above(close, ma_200),
        "pct_above_ma50":  _pct_above(close, ma_50),
    }


def _fmt_pct(value: float | None) -> str:
    return f"{value:+.2f}%" if value is not None else "n/a"


def _build_markdown_report(
    candidates: list[dict[str, Any]],
    run_date: str,
    title: str,
    rule: str,
) -> str:
    lines = [
            f"# {title} \u2014 {run_date}",
        "",
        f"Rule: {rule}",
        "",
        f"Candidates found: {len(candidates)}",
        "",
    ]

    if not candidates:
        lines.append("No candidates matched the rule.")
        return "\n".join(lines)

    lines.extend(
        [
                "| Symbol | Sector | Close | MA50 | % vs MA50 | MA200 | % vs MA200 |",
                "|--------|--------|-------|------|-----------|-------|------------|",
        ]
    )

    for candidate in candidates:
        p = _candidate_payload(candidate)
        lines.append(
            f"| {p['symbol']} "
                f"| {p['sector_name']} "
            f"| {_fmt_float(p['close'])} "
            f"| {_fmt_float(p['ma_50'])} "
            f"| {_fmt_pct(p['pct_above_ma50'])} "
            f"| {_fmt_float(p['ma_200'])} "
            f"| {_fmt_pct(p['pct_above_ma200'])} |"
        )

    return "\n".join(lines)


def _build_telegram_message(
    sector_results: list[dict[str, Any]],  # [{sector_name, rsi_ob_count, gc_count, ma_count, rsi_ob_candidates, gc_candidates}]
    run_date: str,
    ai_summary: str | None = None,
) -> str:
    total_rsi_ob = sum(r["rsi_ob_count"] for r in sector_results)
    total_gc = sum(r["gc_count"] for r in sector_results)
    sectors_with_rsi_ob = [r for r in sector_results if r["rsi_ob_count"] > 0]
    sectors_with_gc = [r for r in sector_results if r["gc_count"] > 0]

    lines = [
        f"📈 *Daily Screen* \u2014 {run_date}",
        f"Sectors screened: {len(sector_results)}",
        "",
        f"1⃣ *RSI Oversold Bounce + Price > MA200*: {total_rsi_ob} candidates",
        f"2⃣ *Golden Cross (last 5 days)*: {total_gc} candidates",
        "",
    ]

    # Summary table — only sectors that have RSI OB or GC candidates
    active = [r for r in sector_results if r["rsi_ob_count"] > 0 or r["gc_count"] > 0]
    if active:
        lines.extend([
            "*Sectors with candidates:*",
            "| Sector | RSI OB | GC |",
            "|--------|--------|----|",
        ])
        for r in active:
            lines.append(f"| {r['sector_name']} | {r['rsi_ob_count']} | {r['gc_count']} |")
        lines.append("")

    # RSI oversold bounce detail — high-priority signal
    if sectors_with_rsi_ob:
        lines.append("🔔 *RSI Oversold Bounce candidates (Price > MA200):*")
        for r in sectors_with_rsi_ob:
            for c in r["rsi_ob_candidates"]:
                p = _candidate_payload(c)
                lines.append(
                    f"- *{p['symbol']}* [{r['sector_name']}]: "
                    f"Close {_fmt_float(p['close'])} | "
                    f"RSI {_fmt_float(p['rsi_14'])} | "
                    f"MA200 {_fmt_float(p['ma_200'])} ({_fmt_pct(p['pct_above_ma200'])})"
                )
        lines.append("")

    # Golden cross detail — list every candidate (rare signal)
    if sectors_with_gc:
        lines.append("🔔 *Golden Cross candidates:*")
        for r in sectors_with_gc:
            for c in r["gc_candidates"]:
                p = _candidate_payload(c)
                lines.append(
                    f"- *{p['symbol']}* [{r['sector_name']}]: "
                    f"Close {_fmt_float(p['close'])} | "
                    f"MA50 {_fmt_float(p['ma_50'])} ({_fmt_pct(p['pct_above_ma50'])}) | "
                    f"MA200 {_fmt_float(p['ma_200'])} ({_fmt_pct(p['pct_above_ma200'])})"
                )
        lines.append("")

    if ai_summary:
        lines.extend(["*AI Analysis*", ai_summary])

    return "\n".join(lines)


def _collect_combined_post_filter_candidates(
    candidates_by_pass: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Collect a stable deduplicated candidate list across all screening passes."""
    combined_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pass_cfg in _PASSES:
        for candidate in candidates_by_pass.get(pass_cfg["strategy"], []):
            symbol = str(candidate.get("symbol", "")).strip().upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                combined_candidates.append(candidate)
    return combined_candidates


def _select_ai_top_pick_shortlist(
    candidates_by_pass: dict[str, list[dict[str, Any]]],
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Build a high-signal shortlist before asking Azure for final top picks."""
    aggregated: dict[str, dict[str, Any]] = {}

    for pass_index, pass_cfg in enumerate(_PASSES):
        strategy = pass_cfg["strategy"]
        weight = len(_PASSES) - pass_index
        for candidate in candidates_by_pass.get(strategy, []):
            symbol = str(candidate.get("symbol", "")).strip().upper()
            if not symbol:
                continue

            indicators = candidate.get("indicators", {})
            entry = aggregated.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "sector_name": candidate.get("sector_name", ""),
                    "reason": candidate.get("reason", ""),
                    "indicators": indicators,
                    "score": 0.0,
                    "pass_hits": [],
                },
            )
            entry["score"] += float(weight)
            entry["pass_hits"].append(strategy)

            try:
                pct_above_ma200 = _pct_above(indicators.get("close"), indicators.get("ma_200"))
                if pct_above_ma200 is not None and pct_above_ma200 > 0:
                    entry["score"] += min(pct_above_ma200, 25.0) / 25.0
            except Exception:
                pass

            try:
                rsi = float(indicators.get("rsi_14"))
                if rsi <= 35:
                    entry["score"] += 0.5
                elif rsi >= 50:
                    entry["score"] += 0.25
            except Exception:
                pass

    ranked = sorted(
        aggregated.values(),
        key=lambda item: (-float(item["score"]), item["symbol"]),
    )
    return ranked[:limit]


def _write_output_files(
    candidates: list[dict[str, Any]],
    run_timestamp: str,
    filename_base: str,
    strategy: str,
    title: str,
    rule: str,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_date = run_timestamp[:10]

    payload = {
        "generated_at": run_timestamp,
        "strategy": strategy,
        "timeframe": "1d",
        "candidate_count": len(candidates),
        "candidates": [_candidate_payload(c) for c in candidates],
    }

    json_path = OUTPUT_DIR / f"{filename_base}.json"
    md_path   = OUTPUT_DIR / f"{filename_base}.md"

    json_path.write_text(json.dumps(payload, indent=2))
    md_path.write_text(_build_markdown_report(candidates, run_date, title, rule))

    LOGGER.info("Wrote %s (%d candidates)", json_path, len(candidates))


_PCT_CSS: dict[bool | None, str] = {True: "pos", False: "neg", None: ""}


def _pct_class(val: float | None) -> str:
    """Return a safe CSS class name for a percentage value ('pos', 'neg', or '')."""
    key = None if val is None else val >= 0
    return _PCT_CSS[key]


def _build_html_page(
    candidates_by_pass: dict[str, list[dict[str, Any]]],
    run_timestamp: str,
    sector_results: list[dict[str, Any]],
) -> str:
    """Build a combined HTML dashboard for GitHub Pages deployment."""
    esc = _html.escape
    run_date = run_timestamp[:10]
    total_rsi_ob = sum(r["rsi_ob_count"] for r in sector_results)
    total_gc = sum(r["gc_count"] for r in sector_results)
    total_ma = sum(r["ma_count"] for r in sector_results)

    css = (
        "body{font-family:system-ui,sans-serif;max-width:1100px;margin:2rem auto;"
        "padding:0 1rem;color:#222}"
        "h1{font-size:1.6rem}"
        "h2{font-size:1.2rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.4rem}"
        "table{border-collapse:collapse;width:100%;margin-top:.6rem;font-size:.9rem}"
        "th,td{border:1px solid #ddd;padding:.45rem .7rem;text-align:left}"
        "th{background:#f5f5f5}"
        "tr:nth-child(even){background:#fafafa}"
        ".meta{color:#666;font-size:.85rem;margin:.3rem 0 1.5rem}"
        ".summary-grid{display:flex;gap:1rem;flex-wrap:wrap;margin:1rem 0 2rem}"
        ".card{border:1px solid #ddd;border-radius:6px;padding:1rem 1.4rem;min-width:140px}"
        ".card-num{font-size:2rem;font-weight:bold}"
        ".card-label{font-size:.85rem;color:#555}"
        ".pos{color:#28a745}.neg{color:#dc3545}"
    )

    cards = (
        '<div class="summary-grid">'
        f'<div class="card"><div class="card-num">{total_rsi_ob}</div>'
        '<div class="card-label">RSI Oversold Bounce + Price &gt; MA200</div></div>'
        f'<div class="card"><div class="card-num">{total_gc}</div>'
        '<div class="card-label">Golden Cross</div></div>'
        f'<div class="card"><div class="card-num">{total_ma}</div>'
        '<div class="card-label">Price &gt; MA50 &amp; MA200</div></div>'
        f'<div class="card"><div class="card-num">{len(sector_results)}</div>'
        '<div class="card-label">Sectors screened</div></div>'
        "</div>"
    )

    active_sectors = [r for r in sector_results if r["rsi_ob_count"] > 0 or r["ma_count"] > 0 or r["gc_count"] > 0]
    if active_sectors:
        sector_rows = "".join(
            f"<tr><td>{esc(r['sector_name'])}</td>"
            f"<td>{r['rsi_ob_count']}</td><td>{r['gc_count']}</td><td>{r['ma_count']}</td></tr>"
            for r in active_sectors
        )
        sector_table = (
            f"<h2>Sectors with Candidates ({len(active_sectors)} of {len(sector_results)})</h2>"
            "<table><thead><tr><th>Sector</th><th>RSI Oversold Bounce</th>"
            "<th>Golden Cross</th><th>MA Rule</th></tr></thead>"
            f"<tbody>{sector_rows}</tbody></table>"
        )
    else:
        sector_table = (
            "<h2>Sectors with Candidates</h2>"
            '<table><tbody><tr><td colspan="4">No candidates in any sector today.</td>'
            "</tr></tbody></table>"
        )

    passes_html = ""
    for pass_cfg in _PASSES:
        strategy = pass_cfg["strategy"]
        candidates = candidates_by_pass.get(strategy, [])
        rows = ""
        for c in candidates:
            p = _candidate_payload(c)
            cls50 = _pct_class(p["pct_above_ma50"])
            cls200 = _pct_class(p["pct_above_ma200"])
            rows += (
                "<tr>"
                f"<td><strong>{esc(p['symbol'])}</strong></td>"
                f"<td>{esc(p['sector_name'])}</td>"
                f"<td>{_fmt_float(p['close'])}</td>"
                f"<td>{_fmt_float(p['rsi_14'])}</td>"
                f"<td>{_fmt_float(p['ma_50'])}</td>"
                f"<td class='{cls50}'>{esc(_fmt_pct(p['pct_above_ma50']))}</td>"
                f"<td>{_fmt_float(p['ma_200'])}</td>"
                f"<td class='{cls200}'>{esc(_fmt_pct(p['pct_above_ma200']))}</td>"
                "</tr>"
            )
        no_results = '<tr><td colspan="8">No candidates matched.</td></tr>'
        passes_html += (
            f"<h2>{esc(pass_cfg['title'])} \u2014 {len(candidates)} candidate(s)</h2>"
            f'<p style="color:#555;font-size:.85rem">Rule: {esc(pass_cfg["rule"])}</p>'
            "<table><thead><tr>"
            "<th>Symbol</th><th>Sector</th><th>Close</th>"
            "<th>RSI 14</th><th>MA50</th><th>% vs MA50</th><th>MA200</th><th>% vs MA200</th>"
            f"</tr></thead><tbody>{rows if rows else no_results}</tbody></table>"
        )

    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>"
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        f"<title>Daily Stock Screen \u2014 {esc(run_date)}</title>"
        f"<style>{css}</style>"
        "</head>\n"
        "<body>\n"
        "<h1>&#x1F4C8; Daily Stock Screen</h1>\n"
        f'<div class="meta">Run date: <strong>{esc(run_date)}</strong>'
        f" &middot; Generated at {esc(run_timestamp)}</div>\n"
        f"{cards}\n"
        f"{sector_table}\n"
        f"{passes_html}\n"
        "</body>\n"
        "</html>\n"
    )


def main() -> None:
    """Screen all sector stocks, persist per-pass outputs, and notify Telegram."""
    load_dotenv()
    settings = Settings.from_env()

    LOGGER.info("Starting daily screen job (sector-driven, 3 passes)")

    data_fetcher   = DataFetcher()
    run_timestamp  = datetime.now(timezone.utc).isoformat()
    run_date       = run_timestamp[:10]

    sectors = _load_all_sectors()
    if not sectors:
        LOGGER.error("No sector data found — aborting. Run monthly_sector_job first.")
        return

    # ── 1. Collect unique symbols across all sectors then bulk-fetch once ─────
    seen: set[str] = set()
    all_symbols: list[str] = []
    for sector in sectors:
        for sym in sector["symbols"]:
            upper = sym.upper()
            if upper not in seen:
                seen.add(upper)
                all_symbols.append(upper)

    LOGGER.info(
        "Bulk-fetching %d unique symbols across %d sectors",
        len(all_symbols), len(sectors),
    )
    data_cache = data_fetcher.get_ohlcv_bulk(all_symbols, period="1y", interval="1d")
    LOGGER.info("Cache ready: %d / %d symbols", len(data_cache), len(all_symbols))

    # ── 2. Build one Screener per pass (all share the same data_cache) ────────
    screeners = {
        pass_cfg["strategy"]: Screener(
            data_fetcher=data_fetcher,
            period="1y",
            interval="1d",
            strategy=pass_cfg["strategy"],
        )
        for pass_cfg in _PASSES
    }

    # Accumulate merged candidates per pass; sector counts for Telegram
    candidates_by_pass: dict[str, list[dict[str, Any]]] = {
        p["strategy"]: [] for p in _PASSES
    }
    sector_results: list[dict[str, Any]] = []

    # ── 3. Screen — no I/O, data already in memory ────────────────────────────
    for sector in sectors:
        sector_id   = sector["sector_id"]
        sector_name = sector["sector_name"]
        symbols     = sector["symbols"]
        LOGGER.info("Sector [%s] %s — %d symbols", sector_id, sector_name, len(symbols))

        pass_counts: dict[str, int] = {}
        rsi_ob_candidates: list[dict[str, Any]] = []
        gc_candidates: list[dict[str, Any]] = []

        for pass_cfg in _PASSES:
            strategy  = pass_cfg["strategy"]
            candidates = screeners[strategy].run_screen(symbols=symbols, data_cache=data_cache)

            # Tag every candidate with its originating sector
            for c in candidates:
                c["sector_name"] = sector_name
                c["sector_id"]   = sector_id

            candidates_by_pass[strategy].extend(candidates)
            pass_counts[strategy] = len(candidates)
            if strategy == "rsi_oversold_bounce_ma200":
                rsi_ob_candidates = candidates
            if strategy == "golden_cross_weekly":
                gc_candidates = candidates

        sector_results.append(
            {
                "sector_id":          sector_id,
                "sector_name":        sector_name,
                "rsi_ob_count":       pass_counts.get("rsi_oversold_bounce_ma200", 0),
                "ma_count":           pass_counts.get("price_above_ma50_ma200", 0),
                "gc_count":           pass_counts.get("golden_cross_weekly", 0),
                "rsi_ob_candidates":  rsi_ob_candidates,
                "gc_candidates":      gc_candidates,
            }
        )

    total_rsi_ob = sum(r["rsi_ob_count"] for r in sector_results)
    total_ma = sum(r["ma_count"] for r in sector_results)
    total_gc = sum(r["gc_count"] for r in sector_results)
    LOGGER.info(
        "Done — RSI OB: %d, Golden Cross: %d, MA: %d across %d sectors",
        total_rsi_ob, total_gc, total_ma, len(sectors),
    )

    # ── 4. Write one merged output file per screening pass ────────────────────
    for pass_cfg in _PASSES:
        strategy = pass_cfg["strategy"]
        _write_output_files(
            candidates_by_pass[strategy],
            run_timestamp,
            pass_cfg["filename_prefix"],
            strategy,
            pass_cfg["title"],
            pass_cfg["rule"],
        )

    # ── 4b. Persist candidates to SQLite for longitudinal analysis ────────────
    db = ScreeningDatabase()
    for pass_cfg in _PASSES:
        strategy = pass_cfg["strategy"]
        db.save_candidates(
            candidates_by_pass[strategy],
            strategy=strategy,
            run_date=run_date,
            run_timestamp=run_timestamp,
        )

    ai_agent = (
        TradingAnalysisAgent(
            project_endpoint=settings.project_endpoint,
            agent_name=settings.agent_name,
        )
        if settings.project_endpoint and settings.agent_name
        else None
    )

    # ── 5. Optional AI summary on golden-cross candidates ─────────────────────
    all_gc = candidates_by_pass["golden_cross_weekly"]
    ai_summary: str | None = None
    if all_gc and ai_agent is not None:
        LOGGER.info("Getting AI summary for %d golden-cross candidates", len(all_gc))
        ai_summary = ai_agent.summarize_screening_results(all_gc)

    msg = _build_telegram_message(sector_results, run_date, ai_summary)
    LOGGER.info("Sending Telegram notification")
    send_markdown_message(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        text=msg,
        message_thread_id=settings.telegram_message_thread_id,
    )

    combined_candidates = _collect_combined_post_filter_candidates(candidates_by_pass)
    ai_shortlist = _select_ai_top_pick_shortlist(candidates_by_pass)
    if combined_candidates and ai_shortlist and ai_agent is not None:
        LOGGER.info(
            "Getting AI top picks from %d combined post-filter tickers via shortlist of %d",
            len(combined_candidates),
            len(ai_shortlist),
        )
        top_picks_message = ai_agent.select_top_tickers_for_telegram(
            ai_shortlist,
            max_tickers=10,
            total_universe_size=len(combined_candidates),
        )
        if top_picks_message:
            LOGGER.info("Sending Telegram top-picks notification")
            send_markdown_message(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
                text=top_picks_message,
                message_thread_id=settings.telegram_message_thread_id,
            )
        else:
            LOGGER.info("Skipping Telegram top-picks notification; Azure response unavailable")

    # ── 5b. Send clean ticker lists to Azure AI Foundry agents ────────────────
    # This is a separate step from the Telegram notification above.
    # Only ticker symbols (no indicator data) are forwarded so that downstream
    # agents such as finrobot stock analyst can fetch and process their own data.
    if ai_agent is not None:
        for pass_cfg in _PASSES:
            strategy = pass_cfg["strategy"]
            tickers = extract_tickers(candidates_by_pass[strategy])
            if tickers:
                LOGGER.info(
                    "Sending %d clean tickers for strategy '%s' to Azure AI Foundry",
                    len(tickers),
                    strategy,
                )
                ai_agent.send_tickers_for_analysis(tickers, strategy=strategy)

    # ── 6. Write HTML dashboard for GitHub Pages ──────────────────────────────
    html_index = OUTPUT_DIR / "index.html"
    html_index.write_text(
        _build_html_page(candidates_by_pass, run_timestamp, sector_results),
        encoding="utf-8",
    )
    LOGGER.info("Wrote %s", html_index)

    LOGGER.info("Daily screen job completed")


if __name__ == "__main__":
    main()
