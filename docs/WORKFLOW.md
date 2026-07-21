# Execution Workflow

This document details the step-by-step execution flow of the system's primary entry points and scheduling.

---

## 1. Unified Daily Update Workflow (`run_daily_update.py`)

Executing `python scripts/run_daily_update.py` triggers the complete transaction and analysis pipeline:

```
[Start Timer] ──> [Run Import Script] ──> [Run Analysis Engine] ──> [Complete & Report]
```

### Phase 1: Scan & Import
1. Checks the folders `data/imports/zerodha/` and `data/imports/groww/` for new broker statement reports.
2. If files are found:
   * Normalizes fields (headers, dates, transaction actions).
   * Compares transaction fingerprints against existing rows to skip duplicates.
   * Auto-assigns chronological IDs (`TRD00001`, `TRD00002`...).
   * Appends new rows to `data/trade_log.csv`.
3. If no files are found, logs `No new trades found.` and continues.
4. If a file is corrupted, stops immediately (exits 1) to prevent data corruption.

### Phase 2: Analysis & Calculations
1. Reads active symbols from the `"Portfolio"` sheet column B.
2. Loads all transaction logs from `data/trade_log.csv`.
3. Performs concurrent lookups of market price details (`yfinance`).
4. Calculates Shares (`qty`) and average purchase cost (`avg_buy`) in-memory.
5. Calculates compound annualized return rates (`get_xirr()`).
6. Triggers technical and fundamental evaluation scoring (`compute_unified_score`).

### Phase 3: Sheets Update & Formula Injection
1. Clears and writes raw rows to `"GITHUB DATA"`.
2. Updates `"Growth Screener"` and watchlist tabs (e.g. `"Future Buy"`).
3. Opens the `"Portfolio"` sheet, scans its headers, and programmatically injects updated VLOOKUP formulas to map `Shares`, `Avg Buy`, `CMP`, `XIRR`, and `Signal` correctly.

### Phase 4: Notifications & Summary
1. Calculates execution runtime.
2. Triggers alerts (Stop Loss breaches, target hits, Strong Buys) to Telegram chat.
3. Prints a clean, formatted summary to the console.

---

## 2. GitHub Actions Automation

* **Trigger**: Scheduled cron job runs daily at `12:30 UTC` (Monday–Friday).
* **Workspace**: Clones the repo, installs dependencies (`pip install -r requirements.txt`), and executes the orchestrator:
  ```bash
  python scripts/run_daily_update.py
  ```
* **Bot polling**: A separate job (`telegram-bot.yml`) runs every 5 minutes to poll and respond to user messages (`python telegram_bot.py`).
