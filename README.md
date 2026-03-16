# stock-util

Public-data stock screening utility that uses Yahoo Finance (no authentication),
computes indicators, summarizes candidates, and sends Telegram notifications.

## Features

- No login required for market data (`yfinance`)
- Technical indicators with `pandas_ta` (EMA, RSI, MACD, ATR)
- Candidate filtering based on configurable screening rules
- Optional Azure AI Foundry summary generation
- Telegram markdown notifications
- Scheduled daily run via GitHub Actions (Mon–Fri 13:00 UTC)
- HTML report automatically deployed to **GitHub Pages** after each run

## Project Layout

```text
src/stock_utils/
  config.py
  data_fetcher.py
  indicators.py
  screener.py
  sector_scraper.py
  ai_agent_client.py
  telegram_notifier.py
  jobs/daily_screen_job.py
data/watchlist.txt
.github/workflows/daily-screen.yml
```

## Quickstart

> Requires Python 3.10 or later. No third-party TA library needed — all indicators are
> implemented with plain `pandas` / `numpy`.

1. Create a virtual environment and install:

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

2. Configure environment variables (create a `.env` file or export them):

```bash
export PROJECT_ENDPOINT="https://<your-foundry-endpoint>"  # optional
export AGENT_NAME="<your-agent-name>"                      # optional
export TELEGRAM_BOT_TOKEN="<bot-token>"                    # optional
export TELEGRAM_CHAT_ID="<chat-id>"                        # optional
export TELEGRAM_MESSAGE_THREAD_ID="1421"                  # optional, e.g. US Stock Signal topic
```

3. Update `data/watchlist.txt` with tickers to screen (one per line).

4. Run from the **project root** (where `pyproject.toml` lives):

```bash
# Daily golden-cross screen (writes data/output/daily-candidates.{json,md})
python -m stock_utils.jobs.daily_screen_job

# Monthly sector sync
python -m stock_utils.jobs.monthly_sector_job

# Weekly sector screen
python -m stock_utils.jobs.weekly_screen_job
```

## Notes

- All indicators (SMA, EMA, RSI, MACD, ATR) are implemented in `indicators.py` using
  plain `pandas` rolling/EWM operations — no `pandas-ta` or `ta-lib` required.
- If Azure settings are missing the app falls back to a local markdown summary.
- Telegram notification is silently skipped when credentials are not set.
- `sector_scraper.py` reads `window.__initState__` JSON embedded by Webull pages.

## GitHub Pages

The daily screening report (`data/output/index.html`) is automatically deployed to
GitHub Pages after each successful workflow run. To enable it:

1. Go to **Settings → Pages** in the repository.
2. Set **Source** to **GitHub Actions**.
3. Trigger the workflow manually (or wait for the next weekday at 13:00 UTC).
4. Once the first run completes successfully, the report URL will be
   `https://<owner>.github.io/<repo>/` and will update on each subsequent run.
