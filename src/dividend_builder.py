import os
import glob
import pandas as pd
import logging

log = logging.getLogger(__name__)

def process_dividends(fund_map):
    """
    Reads Zerodha dividend CSVs, formats transaction data,
    computes year-wise summaries, and returns two sets of rows
    for the 'Dividends' tab.
    """
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    div_dir = os.path.join(PROJECT_ROOT, "data", "imports", "Dividend_Zerodha")
    csv_files = glob.glob(os.path.join(div_dir, "*.csv"))
    
    if not csv_files:
        log.warning(f"No dividend CSVs found in {div_dir}")
        return [], []
        
    dfs = []
    for f in csv_files:
        try:
            df = pd.read_csv(f)
            dfs.append(df)
        except Exception as e:
            log.warning(f"Failed to read {f}: {e}")
            
    if not dfs:
        return [], []
        
    combined = pd.concat(dfs, ignore_index=True)
    
    # Clean column names
    combined.columns = [c.strip() for c in combined.columns]
    
    if 'Total dividend' not in combined.columns:
        log.error("Total dividend column missing from CSVs")
        return [], []
        
    combined['Symbol'] = combined['Symbol'].str.strip()
    
    # Parse dates and extract year
    combined['Ex-date_dt'] = pd.to_datetime(combined['Ex-date'])
    combined['Dividend Year'] = combined['Ex-date_dt'].dt.year
    
    # Sort by Stock -> Ex-Date descending
    combined = combined.sort_values(by=['Symbol', 'Ex-date_dt'], ascending=[True, False])
    
    # 1. Build Transaction Rows
    tx_headers = ["Stock", "Company Name", "Ex-Date", "Quantity", "Dividend Per Share", "Total Dividend", "Dividend Year"]
    tx_rows = [tx_headers]
    
    for _, row in combined.iterrows():
        sym = row['Symbol']
        comp = fund_map.get(sym, {}).get("shortName", "")
        # Original ex-date string if possible, or formatted date
        ex_date_str = row['Ex-date_dt'].strftime('%Y-%m-%d') if pd.notna(row['Ex-date_dt']) else ""
        
        tx_rows.append([
            sym,
            comp,
            ex_date_str,
            row.get('Qty', ""),
            row.get('Dividend per share', ""),
            row.get('Total dividend', ""),
            row.get('Dividend Year', "")
        ])
        
    # 2. Build Year-wise Summary Rows
    # Group by Symbol and Year
    summary = combined.groupby(['Symbol', 'Dividend Year'])['Total dividend'].sum().reset_index()
    
    # Pivot to get years as columns
    pivot = summary.pivot(index='Symbol', columns='Dividend Year', values='Total dividend').fillna(0)
    
    # Sort columns (years) ascending
    years = sorted([c for c in pivot.columns if isinstance(c, (int, float))])
    
    # Calculate Total
    pivot['Total Dividend'] = pivot.sum(axis=1)
    
    # Sort by Total Dividend descending to highlight highest earners
    pivot = pivot.sort_values(by='Total Dividend', ascending=False)
    
    # Build summary rows
    sum_headers = ["Stock"] + [f"{int(y)} Dividend" for y in years] + ["Total Dividend"]
    sum_rows = [sum_headers]
    
    for sym, row in pivot.iterrows():
        r = [sym] + [row[y] for y in years] + [row['Total Dividend']]
        sum_rows.append(r)
        
    return tx_rows, sum_rows


def write_dividends_tab(sh, tx_rows, sum_rows):
    import time
    import gspread.exceptions as _gse
    from sheet_writer import batch_update_safe
    from sheet_formatter import get_structural_format_reqs, hex_rgb

    tab_name = "Dividends"
    # We need 7 columns for tx (A-G), 1 empty separator (H), and len(sum_headers) for sum (I onwards)
    sum_cols = len(sum_rows[0]) if sum_rows else 0
    num_cols = 7 + 1 + sum_cols
    
    try:
        ws = sh.worksheet(tab_name)
    except _gse.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=300, cols=num_cols)

    for _attempt in range(3):
        try:
            ws.clear()
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 2:
                time.sleep(20)
            else:
                raise

    for _attempt in range(5):
        try:
            # Write TX rows to A1
            if tx_rows:
                ws.update('A1', tx_rows)
            # Write SUM rows to I1
            if sum_rows:
                ws.update('I1', sum_rows)
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 4:
                time.sleep(15 * (2 ** _attempt))
            else:
                raise

    ws_id = ws.id
    
    # Structural Formatting
    widths = [90, 200, 90, 70, 120, 100, 100, 30] # A-G + H
    widths += [90] + [100] * (sum_cols - 1) # I onwards
    
    reqs = get_structural_format_reqs(ws_id, max(len(tx_rows), len(sum_rows)), num_cols, widths, freeze_rows=1, freeze_cols=1)

    # ── Heatmap Conditional Formatting for Summary Table ──
    # Columns: I is Stock, J onwards are years, last is Total
    start_col_idx = 8 # Column I
    
    if len(sum_rows) > 1:
        # Create a basic green color scale for the dividend value columns (J through End)
        # using the built-in conditional format rule
        
        rule = {
            "addConditionalFormatRule": {
                "rule": {
                    "ranges": [{
                        "sheetId": ws_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(sum_rows),
                        "startColumnIndex": start_col_idx + 1,
                        "endColumnIndex": start_col_idx + sum_cols
                    }],
                    "gradientRule": {
                        "minpoint": {
                            "color": hex_rgb("ffffff"),
                            "type": "MIN"
                        },
                        "maxpoint": {
                            "color": hex_rgb("388e3c"), # Darker Green
                            "type": "MAX"
                        }
                    }
                },
                "index": 0
            }
        }
        reqs.append(rule)

    # Currency format for Total Dividend and Dividend Per Share
    # D: Qty (3), E: Div/Share (4), F: Total Div (5)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": 1, "endRowIndex": len(tx_rows),
                      "startColumnIndex": 4, "endColumnIndex": 6},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"₹"#,##0.00'}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    })
    
    if sum_rows:
        reqs.append({
            "repeatCell": {
                "range": {"sheetId": ws_id, "startRowIndex": 1, "endRowIndex": len(sum_rows),
                          "startColumnIndex": start_col_idx + 1, "endColumnIndex": start_col_idx + sum_cols},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"₹"#,##0.00'}}},
                "fields": "userEnteredFormat.numberFormat"
            }
        })

    # Run batch update
    batch_update_safe(sh, reqs)
    log.info(f"Wrote {len(tx_rows)} tx rows and {len(sum_rows)} sum rows to {tab_name} tab.")
