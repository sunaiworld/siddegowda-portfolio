# AI Context and Coding Conventions

This file provides system context, architecture boundaries, and coding conventions for AI agents modifying or maintaining this codebase.

---

## 1. System Overview for AI Agents

* **Transaction Ledger**: All transactions are loaded from `data/trade_log.csv`. Do not read transactions from Google Sheets.
* **Calculations**: Average Buy, Quantity (Shares), and XIRR must always be calculated in Python (using `get_avg_buy_and_qty` and `get_xirr`).
* **Presentation**: Pre-calculated metrics are written to the `"GITHUB DATA"` worksheet. The `"Portfolio"` worksheet uses formulas to pull these metrics.
* **Self-Healing Formulas**: Do not assume the column index of the `"Portfolio"` worksheet. Always use `update_portfolio_formulas(sh)` to scan headers dynamically and write formula strings programmatically.

---

## 2. Key Code Maps

* **`TRADE_COLS`** in [src/main.py](file:///c:/Users/Sunai/AppData/Local/Programs/siddegowda-portfolio/src/main.py): Dictionary mapping required keys (`symbol`, `action`, `quantity`, `price`, `date`) to transaction ledger column names. Maintain this mapping when refactoring.
* **`GITHUB_DATA_COLS`** in [src/main.py](file:///c:/Users/Sunai/AppData/Local/Programs/siddegowda-portfolio/src/main.py): Maps GITHUB DATA keys to column index positions. When adding a new column to GITHUB DATA:
  1. Add the column header to the `headers` list in `write_github_data()`.
  2. Append the value to the `row` list in `build_result_row()`.
  3. Register the key and its exact index inside `GITHUB_DATA_COLS`.
  4. Ensure any VLOOKUP indexes or widths are updated accordingly.

---

## 3. Operational Commands

* **Daily Update Workflow**: `python scripts/run_daily_update.py`
* **Direct Import Pipeline**: `python scripts/import_trades.py`
* **Direct Analysis Run**: `python src/main.py`
* **Telegram Bot Poller**: `python telegram_bot.py`
