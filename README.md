# stock-util

Public-data stock screening utility that uses Yahoo Finance (no authentication),
computes indicators, summarizes candidates, and sends Telegram notifications.

## Features

- No login required for market data (`yfinance`)
- Technical indicators with `pandas_ta` (EMA, RSI, MACD, ATR)
- Candidate filtering based on configurable screening rules
- Optional Azure AI Foundry summary generation
- Telegram markdown notifications
- Scheduled run template via GitHub Actions

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
infra/github-actions/daily-screen.yml
```

## Quickstart

1. Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

1. Configure environment variables:

```bash
export PROJECT_ENDPOINT="https://<your-foundry-endpoint>"
export AGENT_NAME="<your-agent-name>"
export TELEGRAM_BOT_TOKEN="<bot-token>"
export TELEGRAM_CHAT_ID="<chat-id>"
```

1. Update `data/watchlist.txt` with tickers to screen.

1. Run the daily job:

```bash
python -m stock_utils.jobs.daily_screen_job
```

## Notes

- If Azure settings are missing, the app falls back to a local markdown summary.
- `sector_scraper.py` is best-effort and may require selector updates if Webull changes HTML.
