# Development Roadmap

This document outlines the planned future milestones and feature enhancements for the portfolio management system.

---

## Short-Term Milestones (Q3 2026)

### 1. Advanced Importer Validation & Alerts
* Add detailed validation error reports to the import pipeline.
* Send an alert to Telegram if a newly committed statement file contains unsupported column types, invalid currencies, or negative transaction counts.

### 2. Extensible Broker Support
* Map CSV layouts for additional major Indian brokerage platforms (e.g. AngelOne, Dhan, Upstox) into `scripts/import_trades.py`.

---

## Medium-Term Milestones (Q4 2026)

### 1. Unified Multi-Asset Tracking
* Extend transaction parsing and yfinance price checks to support Mutual Funds, Gold (SGBs), and Index ETFs.
* Add column identifiers for asset categories in `data/trade_log.csv` and `"GITHUB DATA"`.

### 2. Multi-Currency Holdings
* Support global portfolio assets (e.g., US Equities) by introducing dynamic USD-INR exchange rate conversions inside the `get_avg_buy_and_qty` calculation loop.

---

## Long-Term Milestones (2027)

### 1. Interactive Performance Visualizations
* Upgrade the Google Sheets frontend to generate monthly cash flow charts and asset allocation pie charts based on GITHUB DATA.
* Embed performance chart image dumps directly into Telegram summary notifications.
