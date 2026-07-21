# Project Overview: SiddeGowda Portfolio Auto-Updater

The **SiddeGowda Portfolio Auto-Updater** is a comprehensive, production-grade stock portfolio management system designed to automate trade logging, metric analysis, and reporting. 

It acts as a bridging layer between transaction statements from multiple brokers and a Google Sheets-based visualization dashboard, backed by a Telegram bot for lightweight command-line queries.

---

## Core Value Proposition

Managing investment portfolios across multiple brokerages (e.g., Zerodha, Groww) often leads to fragmented records, manual data entry, and delayed calculations. This system solves these problems by providing:
1. **Unified Transaction Ledger**: A local, append-only master CSV ledger (`data/trade_log.csv`) representing the absolute single source of truth.
2. **Automated Broker Imports**: An extensible, metadata-tolerant import pipeline (`scripts/import_trades.py`) that normalizes statements into the master ledger.
3. **Automated Financial Analysis**: Calculation of stock holdings, average purchase prices, live market values, profit/losses, and cash-flow-based XIRR percentages via Python.
4. **Google Sheets Integration**: A self-healing dashboard reporting real-time metrics, timing scores, and final signals.
5. **On-the-go Telegram Bot Tracking**: A command poller responding instantly to portfolio performance checks and live quote requests.

---

## Repository Boundaries

* **Codebase Language**: Python 3.11+
* **Primary Entry Point**: `scripts/run_daily_update.py`
* **Ledger File**: `data/trade_log.csv`
* **Broker Import Folders**: `data/imports/zerodha/` and `data/imports/groww/`
* **Google Sheet Targets**: `"GITHUB DATA"`, `"Growth Screener"`, `"Future Buy"`, `"Portfolio"`
* **Telegram Interface**: `telegram_bot.py`
