import os
import glob
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

    import math

    def _json_safe(val):
        if pd.isna(val):
            return ""
        if isinstance(val, float):
            if math.isnan(val) or math.isinf(val):
                return ""
        return val

    safe_sum_rows = [[_json_safe(v) for v in row] for row in sum_rows]

    return safe_sum_rows


def write_dividends_tab(sh, sum_rows):
    """
    Writes the year-wise dividend summary — Stock, <year> Dividend...,
    Total Dividend, Amount Invested, Dividend %, Market Dividend Yield % —
    to the Dividends tab, starting at A1. Reuses the same structural
    styling as GITHUB DATA / Portfolio (header, freeze, alternating
    white/light-grey row banding across the full A:J range), applies green
    gradients to the dividend columns and Dividend % only, currency
    formatting to Amount Invested, percentage formatting to Dividend % and
    Market Dividend Yield % (no gradient on the latter — stays neutral),
    and a filter over the full table.
    """
    import time
    import gspread.exceptions as _gse
    from sheet_writer import batch_update_safe
    from sheet_formatter import (
        get_structural_format_reqs,
        hex_rgb,
        get_currency_format_reqs,
        get_percentage_format_reqs,
        get_true_percentage_format_reqs
    )

    tab_name = "Dividends"
    headers = sum_rows[0] if sum_rows else []
    num_cols = len(headers)

    # Locate columns by header name rather than hardcoded offsets, so this
    # stays correct regardless of how many dividend years are present.
    total_idx = headers.index("Total Dividend") if "Total Dividend" in headers else max(num_cols - 3, 0)
    invested_idx = headers.index("Amount Invested") if "Amount Invested" in headers else None
    pct_idx = headers.index("Dividend %") if "Dividend %" in headers else None
    market_yield_idx = headers.index("Market Dividend Yield %") if "Market Dividend Yield %" in headers else None

    try:
        ws = sh.worksheet(tab_name)
    except _gse.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=300, cols=max(num_cols, 10))

    for _attempt in range(3):
        try:
            ws.clear()
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 2:
                time.sleep(20)
            else:
                raise

    # Trim the sheet down to exactly the columns we need.
    try:
        ws.resize(rows=max(len(sum_rows) + 20, 100), cols=max(num_cols, 10))
    except _gse.APIError:
        pass  # non-fatal — formatting below still targets the correct range

    for _attempt in range(5):
        try:
            if sum_rows:
                ws.update('A1', sum_rows)
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 4:
                time.sleep(15 * (2 ** _attempt))
            else:
                raise

    ws_id = ws.id

    # Structural formatting — same header/freeze/row-banding treatment as
    # GITHUB DATA / Portfolio.
    num_year_cols = max(total_idx - 1, 0)
    widths = [90] + [80] * num_year_cols
    if total_idx >= 1:
        widths += [90]
    if invested_idx is not None:
        widths += [100] * (invested_idx - len(widths) + 1)
    if pct_idx is not None:
        widths += [80] * (pct_idx - len(widths) + 1)
    if market_yield_idx is not None:
        widths += [90] * (market_yield_idx - len(widths) + 1)
    widths = (widths + [90] * num_cols)[:num_cols]

    reqs = get_structural_format_reqs(ws_id, len(sum_rows), num_cols, widths, freeze_rows=1, freeze_cols=1)

    # ── Clear any pre-existing conditional format rules on this sheet first,
    # so rules don't accumulate on every daily run.
    try:
        meta = sh.fetch_sheet_metadata()
        sheet_meta = next(
            (s for s in meta.get("sheets", []) if s["properties"]["sheetId"] == ws_id), None
        )
        existing_cf_count = len(sheet_meta.get("conditionalFormats", [])) if sheet_meta else 0
    except Exception as _e:
        log.warning(f"Could not read existing conditional formats, skipping clear: {_e}")
        existing_cf_count = 0

    clear_cf_reqs = [
        {"deleteConditionalFormatRule": {"sheetId": ws_id, "index": i}}
        for i in range(existing_cf_count - 1, -1, -1)
    ]
    reqs = clear_cf_reqs + reqs

    cf_index = 0

    if len(sum_rows) > 1 and total_idx > 1:
        # Yearly dividend columns (B .. column before Total): white -> medium
        # green gradient. Zero values sit at/near the min, so they render
        # neutral/white rather than being treated as a high value.
        reqs.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(sum_rows),
                        "startColumnIndex": 1,
                        "endColumnIndex": total_idx
                    }],
                    "gradientRule": {
                        "minpoint": {"color": hex_rgb("ffffff"), "type": "MIN"},
                        "maxpoint": {"color": hex_rgb("66bb6a"), "type": "MAX"}
                    }
                },
                "index": cf_index
            }
        })
        cf_index += 1

        # Total Dividend column: same family but darker/more prominent.
        reqs.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(sum_rows),
                        "startColumnIndex": total_idx,
                        "endColumnIndex": total_idx + 1
                    }],
                    "gradientRule": {
                        "minpoint": {"color": hex_rgb("ffffff"), "type": "MIN"},
                        "maxpoint": {"color": hex_rgb("1b5e20"), "type": "MAX"}
                    }
                },
                "index": cf_index
            }
        })
        cf_index += 1

    if pct_idx is not None and len(sum_rows) > 1:
        # Dividend % — higher % = stronger green, lower/blank = neutral.
        reqs.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(sum_rows),
                        "startColumnIndex": pct_idx,
                        "endColumnIndex": pct_idx + 1
                    }],
                    "gradientRule": {
                        "minpoint": {"color": hex_rgb("ffffff"), "type": "MIN"},
                        "maxpoint": {"color": hex_rgb("2e7d32"), "type": "MAX"}
                    }
                },
                "index": cf_index
            }
        })
        cf_index += 1

    # Currency format (₹#,##0.00): yearly dividend columns + Total Dividend.
    if total_idx >= 1:
        reqs += get_currency_format_reqs(ws_id, 1, len(sum_rows), 1, total_idx + 1)

    # Currency format for Amount Invested.
    if invested_idx is not None:
        reqs += get_currency_format_reqs(ws_id, 1, len(sum_rows), invested_idx, invested_idx + 1)

    # Percentage format (2 decimals, e.g. 5.25%) for Dividend %.
    if pct_idx is not None:
        reqs += get_percentage_format_reqs(ws_id, 1, len(sum_rows), pct_idx, pct_idx + 1)

    # Percentage format for Market Dividend Yield % — intentionally NO
    # conditional-format gradient here. This is a pure market metric and
    # stays visually neutral (plain alternating white/light-grey banding
    # from the structural formatting above), unlike the dividend-analysis
    # columns which are deliberately shaded green.
    if market_yield_idx is not None:
        reqs += get_true_percentage_format_reqs(ws_id, 1, len(sum_rows), market_yield_idx, market_yield_idx + 1)

    # Filter over the full table (A:J).
    reqs.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": ws_id,
                    "startRowIndex": 0,
                    "endRowIndex": len(sum_rows),
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols
                }
            }
        }
    })

    batch_update_safe(sh, reqs)
    log.info(f"Wrote {len(sum_rows)} summary rows to {tab_name} tab (A:{chr(64 + num_cols)}).")
