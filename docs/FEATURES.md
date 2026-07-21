# System Features

This document provides a comprehensive list of verified features supported by the portfolio management system.

---

## 1. Multi-Broker Importer

* **Extensible Statement Parsing**: Currently supports statement file formats from:
  * **Zerodha**: Standard Tradebook export (CSV format).
  * **Groww**: Standard Tradebook exports (CSV or Excel formats).
* **Header Auto-Detection**: Scans down file rows to dynamically locate starting headers, skipping report metadata/client headers.
* **Intelligent Deduplication**: Utilizes a dual-key index validation logic:
  * *Primary Key*: Checks unique `broker` + `broker_order_id`.
  * *Fallback Signature Key*: If the order ID is missing (e.g. for non-exchange adjustments or specific brokers), it hashes a composite of `(date, symbol, action, quantity, price, broker)` to prevent duplicate imports.
* **Serial ID Sequencing**: Generates a standard chronological `trade_id` (`TRD00001`, `TRD00002`...) continuing from the largest existing transaction number.

---

## 2. Calculation Engine

* **Holdings Tracking**: Filters transaction actions by symbol to compute active share balances (`qty`) and average purchase costs (`avg_buy`) using the First-In-First-Out (FIFO) or standard average cost methods.
* **Annualized Cash Flow Returns (XIRR)**: Computes true investment performance. Handles multiple buy and sell dates, matching cash flows, and live current valuation as the final cash flow.
* **Unified Scoring & Signals**: Evaluates each equity against technical and fundamental metrics to output scoring ratings:
  * *Quality Score*: Financial health metrics (ROE, Debt/Equity, Revenue Growth).
  * *Valuation Score*: Pricing ratios (PE, PB, Dividend Yield).
  * *Timing Score*: Price dynamics (RSI, SMA, Trend, Volume spikes).
  * *Final Action*: Signals such as `BUY`, `STRONG BUY`, `AVOID`, or `SELL`.

---

## 3. Dynamic Google Sheets Dashboard

* **Self-Healing Dashboard Linkages**: Updates sheet formulas dynamically. During runs, it checks headers on the `"Portfolio"` worksheet and writes the correct `VLOOKUP` formula strings, preventing broken indexes.
* **Spreadsheet Layouts**:
  * `"Portfolio"`: Primary dashboard showing shares, values, returns, XIRR, and signals.
  * `"GITHUB DATA"`: The raw output data sheet written by Python.
  * `"Future Buy"`: Automated watchlist updates.
  * `"Growth Screener"`: Filtered high-performing growth stocks.

---

## 4. Telegram Bot Commands

The poller executes command requests instantly via polling loops:
* `/start` / `/help`: Show descriptions and commands.
* `/portfolio`: Pulls instant portfolio summary metrics (value, holdings) from the cached sheets.
* `/buy`: Filters and returns current active `BUY` and `STRONG BUY` symbols.
* `/sell`: Filters and returns active `SELL` and `TRIM` recommendations.
* `/top`: Lists the top 5 high-scoring equities.
* `/price <symbol>`: Retrieves a live snapshot quote of any stock ticker.
* `/refresh`: Runs the complete sheets update pipeline (price lookups, XIRR calculations, Sheets update) in real-time.
