# scripts/update_portfolio_tab.py
"""
Writes live holdings into the "Portfolio" worksheet of the GitHub_Test
Google Sheet, sourced from data/trade_log.csv (already produced by
scripts/import_trades.py from the Zerodha + Groww broker exports under
data/imports/).

Columns written (C:N) - Quantity, Avg Buy Price, Current Price,
Invested Value, Current Value, P/L, P/L%, Sector, Mkt Cap (Cr),
Cap Type, XIRR%, Last Updated.

Column A is never touched. Column B is read/used as the existing
Symbol column - the same convention main.read_symbols() already
relies on (`row[1]` = col B) - so this script and the existing daily
scoring pipeline agree on where the symbol lives. A symbol already
present in col B gets its C:N range updated in place; a symbol from
trade_log.csv with no existing row gets appended (col A left blank).
Nothing is deleted, so any manual notes in column A or extra columns
beyond N survive untouched.

Reuses instead of reimplementing:
- main.get_gspread_client / main.SHEET_ID        - Sheets auth
- main.get_avg_buy_and_qty                        - average-cost qty/price
- main.get_xirr                                   - per-symbol XIRR
- main.fetch_prices_batch                         - batched yfinance CMP
- main.FUNDAMENTALS_CACHE_DAYS
- fund_cache.load_cache / get_or_fetch_fundamentals / save_cache
  - the SAME "Fundamentals Cache" Sheets tab src/main.py's daily run
  reads/writes, so running this step first costs zero extra yfinance
  fundamentals calls that day.

Run order (see .github/workflows/daily_update.yml):
    1. scripts/import_trades.py         imports/ -> data/trade_log.csv
    2. scripts/update_portfolio_tab.py  trade_log.csv -> Portfolio tab (this file)
    3. src/main.py                      Portfolio tab col B -> GITHUB DATA / Growth
                                         Screener / Dashboard / watchlists
"""
import csv
import sys
import time
import logging
from pathlib import Path

project_root = Path(__file__).parents[1]
sys.path.append(str(project_root))
sys.path.append(str(project_root / "src"))

import main as m           # noqa: E402  (reuse, not reimplement)
import fund_cache          # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TRADE_LOG_CSV = project_root / "data" / "trade_log.csv"
PORTFOLIO_TAB = "Portfolio"

# 1-indexed sheet columns this script owns. Column A (1) and B (2,
# Symbol) are never written here except appending B for a brand-new
# holding row.
COLS = {
    "qty": 3, "avg_buy": 4, "cmp": 5, "invested": 6, "current": 7,
    "pl": 8, "pl_pct": 9, "sector": 10, "mcap": 11, "cap_type": 12,
    "xirr": 13, "updated": 14,
}
HEADERS = {
    "qty": "Quantity", "avg_buy": "Avg Buy Price", "cmp": "Current Price",
    "invested": "Invested Value", "current": "Current Value",
    "pl": "P/L (\u20b9)", "pl_pct": "P/L %", "sector": "Sector",
    "mcap": "Mkt Cap (Cr)", "cap_type": "Cap Type", "xirr": "XIRR %",
    "updated": "Last Updated",
}


def _load_trades():
    """
    Read data/trade_log.csv and reshape each row into the 5-field tuple
    (symbol, date, action, quantity, price) that main.get_avg_buy_and_qty()
    and main.get_xirr() already expect - the same shape as raw Trade Log
    sheet rows, so those two functions run completely unmodified here.
    """
    if not TRADE_LOG_CSV.is_file():
        log.error(f"{TRADE_LOG_CSV} not found - run scripts/import_trades.py first")
        return []

    trades = []
    with open(TRADE_LOG_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = row.get("symbol", "").strip().upper()
            if not sym:
                continue
            trades.append((
                sym,
                row.get("date", ""),
                row.get("action", ""),
                row.get("quantity", "0"),
                row.get("price", "0"),
            ))
    return trades


def write_portfolio_tab(sh, holdings):
    try:
        ws = sh.worksheet(PORTFOLIO_TAB)
    except Exception:
        ws = sh.add_worksheet(PORTFOLIO_TAB, rows=200, cols=14)
        ws.update_acell("B1", "Symbol")

    all_vals = ws.get_all_values()
    if not all_vals:
        all_vals = [["", "Symbol"]]

    # Existing Symbol (col B) -> sheet row number (1-indexed)
    row_by_symbol = {}
    for i, row in enumerate(all_vals[1:], start=2):
        sym = row[1].strip().upper() if len(row) > 1 else ""
        if sym:
            row_by_symbol[sym] = i

    now_str = m.datetime.now().strftime("%d-%b-%Y %H:%M")
    updates = []      # gspread batch_update value ranges
    new_rows = []     # symbols with no existing row -> appended

    for sym, h in sorted(holdings.items()):
        qty, avg_buy, cmp = h["qty"], h["avg_buy"], h.get("cmp")
        f = h.get("fund") or {}

        invested = round(qty * avg_buy, 2) if avg_buy else ""
        current = round(qty * cmp, 2) if cmp else ""
        pl = round(current - invested, 2) if (current != "" and invested != "") else ""
        pl_pct = round(pl / invested * 100, 2) if (pl != "" and invested not in ("", 0)) else ""

        mcap_cr = f.get("mcap_cr")
        cap_type = ""
        if mcap_cr:
            if mcap_cr >= 25000:
                cap_type = "Large Cap"
            elif mcap_cr >= 5000:
                cap_type = "Mid Cap"
            else:
                cap_type = "Small Cap"

        values = [
            qty, avg_buy or "", cmp or "", invested, current, pl, pl_pct,
            f.get("sector", ""), mcap_cr or "", cap_type,
            h.get("xirr") if h.get("xirr") is not None else "", now_str,
        ]

        if sym in row_by_symbol:
            r = row_by_symbol[sym]
            updates.append({"range": f"C{r}:N{r}", "values": [values]})
        else:
            new_rows.append(["", sym] + values)

    # Header row for the columns this script owns (C1:N1) - A1/B1 untouched.
    header_row = [HEADERS[k] for k in sorted(COLS, key=lambda k: COLS[k])]
    updates.insert(0, {"range": "C1:N1", "values": [header_row]})

    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")

    log.info(
        f"Portfolio tab: {max(len(updates) - 1, 0)} existing rows updated, "
        f"{len(new_rows)} new rows appended"
    )


def main():
    trades = _load_trades()
    if not trades:
        log.error("No trades found - aborting Portfolio tab update")
        return

    symbols = sorted({t[0] for t in trades})
    log.info(f"{len(symbols)} distinct symbols in trade_log.csv")

    # Qty + avg buy price per symbol - reuses main.py's cost-basis math
    holdings = {}
    for sym in symbols:
        avg_buy, qty = m.get_avg_buy_and_qty(sym, trades)
        if qty > 0:
            holdings[sym] = {"qty": qty, "avg_buy": avg_buy}
    log.info(f"{len(holdings)} open positions (qty > 0)")

    if not holdings:
        log.warning("No open positions - nothing to write")
        return

    gc = m.get_gspread_client()
    sh = gc.open_by_key(m.SHEET_ID)

    # Current prices - reuses main.py's batched yfinance fetch
    prices = m.fetch_prices_batch(list(holdings.keys()))

    # Sector / market cap - reuses the SAME Fundamentals Cache tab
    # src/main.py's daily run reads/writes, so no duplicate yfinance
    # load if this runs right before src/main.py in the same CI job.
    fc_cache = fund_cache.load_cache(sh)
    for sym in holdings:
        holdings[sym]["fund"] = fund_cache.get_or_fetch_fundamentals(
            sym, fc_cache, max_age_days=m.FUNDAMENTALS_CACHE_DAYS
        )
        time.sleep(1)
    fund_cache.save_cache(sh, fc_cache)

    # XIRR per symbol - reuses main.py's XIRR solver
    for sym, h in holdings.items():
        cmp = prices.get(sym)
        h["cmp"] = cmp
        h["xirr"] = m.get_xirr(sym, trades, cmp) if cmp else None

    write_portfolio_tab(sh, holdings)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"\u274c Portfolio tab update failed: {e}\n")
        sys.exit(1)
