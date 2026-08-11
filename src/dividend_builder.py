import os
import glob
import pandas as pd
import logging

log = logging.getLogger(__name__)

def process_dividends(fund_map):
    """
    Reads Zerodha dividend CSVs and computes the year-wise dividend
    summary for the 'Dividends' tab (Stock, <year> Dividend..., Total
    Dividend), sorted by Total Dividend descending.

    The per-transaction detail (Ex-Date/Qty/Div-per-share) is used only
    internally to compute the yearly sums — it is no longer written to
    the sheet.
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

    # Calculate Total
    pivot['Total Dividend'] = pivot.sum(axis=1)

    # Sort by Total Dividend descending to highlight highest earners
    pivot = pivot.sort_values(by='Total Dividend', ascending=False)

    # Build summary rows: Stock | <year> Dividend ... | Total Dividend
    sum_headers = ["Stock"] + [f"{int(y)} Dividend" for y in years] + ["Total Dividend"]
    sum_rows = [sum_headers]

    for sym, row in pivot.iterrows():
        r = [sym] + [row[y] for y in years] + [row['Total Dividend']]
        sum_rows.append(r)

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
    Writes ONLY the year-wise dividend summary (Stock, <year> Dividend...,
    Total Dividend) to the Dividends tab, starting at A1. Reuses the same
    structural styling as GITHUB DATA / Portfolio (header, freeze, banding),
    applies a green gradient heatmap to the dividend columns (B onwards),
    and adds a filter over the full table.
    """
    import time
    import gspread.exceptions as _gse
    from sheet_writer import batch_update_safe
    from sheet_formatter import get_structural_format_reqs, get_currency_format_reqs, hex_rgb

    tab_name = "Dividends"
    num_cols = len(sum_rows[0]) if sum_rows else 0

    try:
        ws = sh.worksheet(tab_name)
    except _gse.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=300, cols=max(num_cols, 7))

    for _attempt in range(3):
        try:
            ws.clear()
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 2:
                time.sleep(20)
            else:
                raise

    # Trim the sheet down to exactly the columns we need — the old A:H
    # transaction table + I:O summary layout no longer exists, so there's
    # no reason to keep the sheet 15 columns wide.
    try:
        ws.resize(rows=max(len(sum_rows) + 20, 100), cols=max(num_cols, 7))
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
    widths = [130] + [115] * (num_cols - 2) + [130] if num_cols >= 2 else [130] * num_cols
    reqs = get_structural_format_reqs(ws_id, len(sum_rows), num_cols, widths, freeze_rows=1, freeze_cols=1)

    # ── Clear any pre-existing conditional format rules on this sheet first.
    # The previous implementation always inserted a new gradient rule at
    # index 0 without ever removing the old one, so rules silently piled up
    # on every daily run. Clear before re-adding so there's exactly one set.
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

    if len(sum_rows) > 1 and num_cols >= 2:
        # Yearly dividend columns (B .. second-to-last): white -> medium
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
                        "endColumnIndex": num_cols - 1
                    }],
                    "gradientRule": {
                        "minpoint": {"color": hex_rgb("ffffff"), "type": "MIN"},
                        "maxpoint": {"color": hex_rgb("66bb6a"), "type": "MAX"}
                    }
                },
                "index": 0
            }
        })

        # Total Dividend column: same family but darker/more prominent,
        # so the highest total-dividend payers pop out immediately.
        reqs.append({
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(sum_rows),
                        "startColumnIndex": num_cols - 1,
                        "endColumnIndex": num_cols
                    }],
                    "gradientRule": {
                        "minpoint": {"color": hex_rgb("ffffff"), "type": "MIN"},
                        "maxpoint": {"color": hex_rgb("1b5e20"), "type": "MAX"}
                    }
                },
                "index": 1
            }
        })

    # Currency format (₹#,##0.00) for all dividend value columns, B onwards.
    if num_cols >= 2:
        reqs += get_currency_format_reqs(ws_id, 1, len(sum_rows), 1, num_cols)

    # Filter over the full table.
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
