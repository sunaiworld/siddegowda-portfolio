import os
import glob
import math
import pandas as pd
import logging
from profiler import profiler

log = logging.getLogger(__name__)


def _get_invested_map(imports_dir="data/imports"):
    """
    Returns {symbol: total_invested_amount}, using the exact same cost-basis
    source the Portfolio tab uses: portfolio_builder.compute_holdings() over
    portfolio_builder.load_all_trades() (true cost basis from broker trade
    history — never CMP x qty, never an estimate). Summed across brokers per
    symbol, mirroring how build_portfolio()'s "combined" view aggregates
    Invested. No duplicate calculation logic — this reuses the same
    functions the Portfolio tab is built from.
    """
    try:
        from portfolio_builder import load_all_trades, compute_holdings
    except Exception as e:
        log.warning(f"Could not import portfolio_builder for invested amounts: {e}")
        return {}

    try:
        trades = load_all_trades(imports_dir)
        holdings = compute_holdings(trades)  # keyed "broker:symbol"
    except Exception as e:
        log.warning(f"Could not compute holdings for invested amounts: {e}")
        return {}

    invested = {}
    for h in holdings.values():
        sym = h.get("symbol", "")
        cost = h.get("cost", 0.0)
        if not sym:
            continue
        invested[sym] = invested.get(sym, 0.0) + cost
    return invested


def process_dividends(fund_map):
    """
    Reads Zerodha dividend CSVs and computes the year-wise dividend
    summary for the 'Dividends' tab:

        Stock | <year> Dividend ... | Total Dividend | Amount Invested
        | Dividend % | Market Dividend Yield %

    sorted by Total Dividend descending. Amount Invested is the true cost
    basis reused from the Portfolio tab's data source (see
    _get_invested_map); Dividend % = Total Dividend / Amount Invested x 100
    (historical dividend received relative to invested capital — a personal
    return metric). Market Dividend Yield % is a completely separate,
    portfolio-independent market metric (Annual Dividend Per Share / CMP x
    100) reused as-is from fund_map[sym]["div"] — the same yfinance-based
    field fetch_fundamentals() already computes and main.py already passes
    into this function. If a stock has no investment data, Amount Invested
    and Dividend % are left blank; if fund_map has no dividend-yield data
    for a stock, Market Dividend Yield % is left blank. Nothing is
    estimated.

    The per-transaction detail (Ex-Date/Qty/Div-per-share) is used only
    internally to compute the yearly sums — it is not written to the sheet.
    """
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    div_dir = os.path.join(PROJECT_ROOT, "data", "imports", "Dividend_Zerodha")
    csv_files = glob.glob(os.path.join(div_dir, "*.csv"))

    if not csv_files:
        log.warning(f"No dividend CSVs found in {div_dir}")
        return []

    dfs = []
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            dfs.append(df)
        except Exception as e:
            log.warning(f"Failed to read {f}: {e}")

    if not dfs:
        return []

    combined = pd.concat(dfs, ignore_index=True)

    # Clean column names
    combined.columns = [c.strip() for c in combined.columns]

    if 'Total dividend' not in combined.columns:
        log.error("Total dividend column missing from CSVs")
        return []

    combined['Symbol'] = combined['Symbol'].str.strip()

    # Parse dates and extract year
    combined['Ex-date_dt'] = pd.to_datetime(combined['Ex-date'])
    combined['Dividend Year'] = combined['Ex-date_dt'].dt.year

    # Year-wise Summary: group by Symbol and Year
    summary = combined.groupby(['Symbol', 'Dividend Year'])['Total dividend'].sum().reset_index()

    # Pivot to get years as columns
    pivot = summary.pivot(index='Symbol', columns='Dividend Year', values='Total dividend').fillna(0)

    # Sort columns (years) ascending
    years = sorted([c for c in pivot.columns if isinstance(c, (int, float))])

    # Calculate Total (unchanged — existing dividend calc/logic untouched)
    pivot['Total Dividend'] = pivot.sum(axis=1)

    # Sort by Total Dividend descending to highlight highest earners
    pivot = pivot.sort_values(by='Total Dividend', ascending=False)

    invested_map = _get_invested_map()

    # Build summary rows: Stock | <year> Dividend ... | Total Dividend | Amount Invested | Dividend % | Market Dividend Yield %
    sum_headers = (
        ["Stock"] + [f"{int(y)} Dividend" for y in years]
        + ["Total Dividend", "Amount Invested", "Dividend %", "Market Dividend Yield %"]
    )
    sum_rows = [sum_headers]

    for sym, row in pivot.iterrows():
        total_div = row['Total Dividend']
        invested = invested_map.get(sym)
        if invested and invested > 0:
            div_pct = round((total_div / invested) * 100, 2)
        else:
            invested = ""
            div_pct = ""

        # Market Dividend Yield % is a pure market metric (Annual Dividend
        # Per Share / CMP x 100) — independent of holdings/qty/invested
        # amount. Reused as-is from fetch_fundamentals()'s existing
        # yfinance-based "div" field (data_fetcher.py) rather than
        # recomputed here, so this is never a second competing calculation.
        market_yield = (fund_map or {}).get(sym, {}).get("div")
        if market_yield in (None, ""):
            market_yield = ""
        else:
            try:
                my_val = float(market_yield)
                # The value stored in fund_map is formatted for GITHUB DATA (e.g. 4.03).
                # Google Sheets genuine percentage format (0.00%) requires a decimal fraction.
                # So we normalize exactly once here: 4.03 -> 0.0403
                market_yield = my_val / 100.0
            except (ValueError, TypeError):
                pass

        r = [sym] + [row[y] for y in years] + [total_div, invested, div_pct, market_yield]
        sum_rows.append(r)
        profiler.increment("Rows written")

    def _json_safe(val):
        try:
            if pd.isna(val):
                return ""
        except Exception:
            pass
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return ""
        return val

    safe_sum_rows = [[_json_safe(v) for v in row] for row in sum_rows]

    return safe_sum_rows


def write_dividends_tab(sh, sum_rows, fund_map=None):
    """
    Writes the year-wise dividend summary to the Dividends tab.

    Uses the IDENTICAL formatting system as GITHUB DATA (write_github_data):
    - get_structural_format_reqs: header (#0d1b2a, white bold fs8, WRAP),
      freeze row1/col1, header height 50px, alternating rows f8f9fa/ffffff
    - get_currency_format_reqs / get_percentage_format_reqs: number formats
    - color_cell_req per data row for ALL conditional colours
      (NO addConditionalFormatRule — same approach as write_github_data)
    - setBasicFilter over full table

    Order: structural → number formats → per-cell colours → filter
    This matches write_github_data exactly and guarantees colours appear.
    """
    import gspread.exceptions as _gse
    from sheet_writer import batch_update_safe
    from sheet_formatter import (
        get_structural_format_reqs,
        get_currency_format_reqs,
        get_percentage_format_reqs,
        get_true_percentage_format_reqs,
        color_cell_req,
    )

    tab_name = "Dividends"
    headers = sum_rows[0] if sum_rows else []
    num_cols = len(headers)

    # Locate columns by header name — stays correct regardless of year count
    total_idx        = headers.index("Total Dividend")          if "Total Dividend"          in headers else max(num_cols - 4, 0)
    invested_idx     = headers.index("Amount Invested")         if "Amount Invested"         in headers else None
    pct_idx          = headers.index("Dividend %")              if "Dividend %"              in headers else None
    market_yield_idx = headers.index("Market Dividend Yield %") if "Market Dividend Yield %" in headers else None

    try:
        ws = sh.worksheet(tab_name)
    except _gse.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=max(len(sum_rows) + 20, 100), cols=max(num_cols, 10))

    ws.clear()
    ws.update("A1", sum_rows)
    ws_id = ws.id

    # ── Column widths (GITHUB DATA compact philosophy) ─────────────────────
    # Stock: 90, each year-dividend col: 75, Total Dividend: 85,
    # Amount Invested: 90, Dividend %: 70, Market Dividend Yield %: 80
    num_year_cols = max(total_idx - 1, 0)
    widths = (
        [90]                     # Stock
        + [75] * num_year_cols   # Year dividend columns
        + [85]                   # Total Dividend
    )
    if invested_idx is not None:
        widths += [90]           # Amount Invested
    if pct_idx is not None:
        widths += [70]           # Dividend %
    if market_yield_idx is not None:
        widths += [80]           # Market Dividend Yield %
    widths = (widths + [75] * num_cols)[:num_cols]

    # ── 1. Structural format ─────────────────────────────────────────────────
    # Same call as write_github_data: header dark bg, white bold fs8, WRAP,
    # freeze row1+col1, header height 50px, alternating f8f9fa/ffffff rows.
    reqs = get_structural_format_reqs(
        ws_id, len(sum_rows), num_cols, widths, freeze_rows=1, freeze_cols=1
    )

    # ── 2. Number formats ────────────────────────────────────────────────────
    # Currency (₹#,##0.00): year dividend cols + Total Dividend
    if total_idx >= 1:
        reqs += get_currency_format_reqs(ws_id, 1, len(sum_rows), 1, total_idx + 1)
    # Currency for Amount Invested
    if invested_idx is not None:
        reqs += get_currency_format_reqs(ws_id, 1, len(sum_rows), invested_idx, invested_idx + 1)
    # Number format "0.00%" for Dividend % (stored as plain number e.g. 5.25)
    if pct_idx is not None:
        reqs += get_percentage_format_reqs(ws_id, 1, len(sum_rows), pct_idx, pct_idx + 1)
    # True percentage format "0.00%" for Market Dividend Yield % (stored as 0.0403)
    if market_yield_idx is not None:
        reqs += get_true_percentage_format_reqs(ws_id, 1, len(sum_rows), market_yield_idx, market_yield_idx + 1)

    # ── 3. Per-cell colour coding — identical approach to write_github_data ──
    # No addConditionalFormatRule. Direct color_cell_req per row, applied
    # after the structural background so they take visual precedence.
    def _safe_float(val):
        if val in (None, ""):
            return None
        try:
            f = float(val)
            return None if math.isnan(f) or math.isinf(f) else f
        except (TypeError, ValueError):
            return None

    for i, row in enumerate(sum_rows[1:], start=1):
        rn = i  # 0-indexed row index; row 0 = header, row 1 = first data row

        # Stock column — cap-type colour (same logic as GITHUB DATA cap_type)
        sym = str(row[0]).strip()
        if fund_map:
            mcap_f = _safe_float(fund_map.get(sym, {}).get("mcap"))
            if mcap_f is not None:
                if mcap_f >= 25000:
                    reqs.append(color_cell_req(ws_id, rn, 0, "d9ead3", "0b8043"))
                elif mcap_f >= 5000:
                    reqs.append(color_cell_req(ws_id, rn, 0, "d9eaf7", "1565c0"))
                else:
                    reqs.append(color_cell_req(ws_id, rn, 0, "fde9d9", "c62828"))

        # Dividend % — same thresholds as GITHUB DATA div_v (stored as plain number)
        if pct_idx is not None:
            pct_val = _safe_float(row[pct_idx])
            if pct_val is not None:
                if pct_val >= 2:
                    reqs.append(color_cell_req(ws_id, rn, pct_idx, "d9ead3", "0b8043"))
                elif pct_val >= 1:
                    reqs.append(color_cell_req(ws_id, rn, pct_idx, "fff2cc", "7f4f00"))

        # Market Dividend Yield % — stored as fraction (0.0403 = 4.03%)
        # thresholds: >= 0.02 (2%) green, >= 0.01 (1%) yellow
        if market_yield_idx is not None:
            my_val = _safe_float(row[market_yield_idx])
            if my_val is not None:
                if my_val >= 0.02:
                    reqs.append(color_cell_req(ws_id, rn, market_yield_idx, "d9ead3", "0b8043"))
                elif my_val >= 0.01:
                    reqs.append(color_cell_req(ws_id, rn, market_yield_idx, "fff2cc", "7f4f00"))

    # ── 4. Filter over full table ─────────────────────────────────────────────
    reqs.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": ws_id,
                    "startRowIndex": 0,
                    "endRowIndex": len(sum_rows),
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                }
            }
        }
    })

    batch_update_safe(sh, reqs)
    log.info(f"Wrote {len(sum_rows)} summary rows to {tab_name} tab (A:{chr(64 + num_cols)}).")
