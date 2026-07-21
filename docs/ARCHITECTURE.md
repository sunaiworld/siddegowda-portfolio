# System Architecture

This document describes the design layout, dependencies, data-flow, and design decisions of the portfolio management system.

---

## 1. High-Level Architecture (Option B Design)

The system is split into two logical layers:
* **The Calculation Engine (Python Backend)**: Handles parsing, deduplication, financial math (Holdings, Avg Buy, XIRR), and real-time market data retrieval (via `yfinance`).
* **The Presentation Layer (Google Sheets Frontend)**: Provides visual dashboards and manual user notes.

```
 [ Raw Statement Files ]
         │ (Zerodha CSV / Groww CSV/Excel)
         ▼
 ┌────────────────────────────────────────────────────────┐
 │ 1. Trade Importer (import_trades.py)                   │
 └───────────────────────┬────────────────────────────────┘
                         ▼
             [ data/trade_log.csv ] (Master Ledger)
                         │
                         ▼
 ┌────────────────────────────────────────────────────────┐
 │ 2. Analysis Engine (src/main.py)                      │
 │    - Reads trades & fetches CMP via yfinance           │
 │    - Calculates Shares, Avg Buy, & XIRR in Python      │
 └───────────────────────┬────────────────────────────────┘
                         ▼
             [ Google Sheets: GITHUB DATA ] (Pre-calculated metrics)
                         │
                         ▼
             [ Google Sheets: Portfolio ] (Pulls via self-healing VLOOKUPs)
```

---

## 2. Design Decisions & Trade-offs

### The Single Source of Truth
The master transaction ledger is `data/trade_log.csv`. 
Google Sheets is **read-only** for transactions. The sheet formulas do not calculate shares or purchase costs from a Google Sheets-based log. Instead, Python performs all calculations in-memory and pushes the finalized metrics to a hidden/reference worksheet (`GITHUB DATA`). The dashboard references this tab.

### Self-Healing Sheet Formulas
To prevent column drift and broken formula links (such as when inserting columns like `Day Chg%` or re-ordering columns), the engine dynamically updates worksheet cells:
1. It opens the `"Portfolio"` sheet.
2. Scans the column headers dynamically to find where `Symbol`, `Shares`, `Avg Buy`, `Current Price`, `XIRR`, and `Signal` reside.
3. Automatically writes the exact, corrected `VLOOKUP` formula strings targeting `"GITHUB DATA"`.
This completely eliminates `#REF!`, `#N/A`, and index shifts on the dashboard.

---

## 3. Tech Stack & Dependencies

* **Core Language**: Python 3.11+
* **Market Data Service**: `yfinance` (real-time/historical price feeds)
* **Google Drive API Client**: `gspread` & `google-auth` (for spreadsheet updates)
* **Message Broker Service**: `python-telegram-bot` (command dispatch and alerts)
* **Utility Libraries**: `pandas` and `openpyxl` (optionally loaded for Excel imports)
