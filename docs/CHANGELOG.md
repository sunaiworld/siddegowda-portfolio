# Changelog

This document logs all historical milestones and system modifications.

---

## [2.1.0] - 2026-07-21
### Added
* Created `scripts/import_trades.py` to automate raw broker statement imports for Zerodha (CSVs) and Groww (CSVs/Excel sheets) with signature deduplication, field normalization, and auto-incrementing trade IDs.
* Created `scripts/run_daily_update.py` orchestrator to run imports, handle process exit code signals, update calculations, and write to Google Sheets under a single command.
* Created automated testing scripts `test_importer.py`, `test_migration.py`, and `test_daily_update.py` to verify pipelines.

### Changed
* **Option B Sheets Integration**:
  * Moved `Quantity` (Shares) and `Avg Buy Price` calculations from Google Sheets formulas to the Python analysis engine.
  * Appended these values directly to GITHUB DATA worksheet outputs (expanding GITHUB DATA columns to 38).
  * Implemented `update_portfolio_formulas(sh)` in `src/main.py` to dynamically rewrite correct, self-healing VLOOKUP formulas to the `Portfolio` sheet, resolving historical column index shifts.
* **DictReader Refactoring**:
  * Refactored `read_trades()` in `src/main.py` to use `csv.DictReader` and dictionary field lookups (`TRADE_COLS`), decoupling the calculations from transaction CSV column ordering.

---

## [2.0.0] - 2026-07-13
### Added
* Initial codebase for SiddeGowda Portfolio auto-updater.
* Setup of Google Sheets integration worksheets (`Portfolio`, `GITHUB DATA`, `Growth Screener`, `Future Buy`).
* Basic scoring engine evaluating fundamental and technical parameters.
* Telegram bot command poller interface (`telegram_bot.py`).
* Scheduled daily update workflows on GitHub Actions.
