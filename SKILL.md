---
name: stock-screening-public
description: >
  Python-based stock screening pipeline using PUBLIC data sources (Yahoo Finance),
  technical indicators, Azure AI Foundry agents, and Telegram notifications.
  NO authentication or login required.
license: MIT
---

# Context

You are GitHub Copilot working inside the stock-utilities repository.

This repo implements a daily stock screening system using public data only:

- Fetches stock data from Yahoo Finance via yfinance (no login required)
- Optionally scrapes Webull Hot Sectors public page for sector lists
- Computes technical indicators (EMA, RSI, MACD, ATR) with pandas and pandas_ta
- Applies screening rules to produce shortlist
- Calls Azure AI Foundry Agent for analysis
- Sends report via Telegram bot

# Project Structure

- src/stock_utils/data_fetcher.py
- src/stock_utils/sector_scraper.py
- src/stock_utils/indicators.py
- src/stock_utils/screener.py
- src/stock_utils/ai_agent_client.py
- src/stock_utils/telegram_notifier.py
- src/stock_utils/jobs/daily_screen_job.py
- data/watchlist.txt

# Working Rules

- Use yfinance for stock data fetching
- Do not add login/auth logic for data sources
- Keep screening logic separate from data fetching
- Include type hints and docstrings
- Handle per-symbol failures gracefully without crashing the full run
