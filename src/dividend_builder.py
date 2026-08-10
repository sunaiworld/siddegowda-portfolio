import os
import glob
import pandas as pd
import logging
from config import PROJECT_ROOT

log = logging.getLogger(__name__)

def process_dividends(prices, fund_map):
    """
    Reads Zerodha dividend CSVs, merges with fundamental/price data,
    computes scores, and returns a list of rows for the 'Dividends' tab.
    """
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
    
    # We expect columns: 'Symbol', 'Ex-date', 'Qty', 'Dividend per share', 'Total dividend'
    if 'Total dividend' not in combined.columns:
        log.error("Total dividend column missing from CSVs")
        return [], []
        
    # Standardize Symbol
    combined['Symbol'] = combined['Symbol'].str.strip()
    
    # Calculate historical metrics from CSVs
    # Total dividend received per symbol
    total_received = combined.groupby('Symbol')['Total dividend'].sum().to_dict()
    
    # Distinct years of dividend
    combined['Year'] = pd.to_datetime(combined['Ex-date']).dt.year
    distinct_years = combined.groupby('Symbol')['Year'].nunique().to_dict()
    
    unique_symbols = combined['Symbol'].unique()
    
    rows = []
    for sym in unique_symbols:
        fund = fund_map.get(sym, {})
        cmp = prices.get(sym, 0)
        
        # Identification
        sector = fund.get("sector", "N/A")
        industry = fund.get("industry", "N/A")
        
        # Dividend Metrics
        current_yield = fund.get("div") or 0.0
        hist_yield_5y = fund.get("div_yield_5y") or 0.0
        div_rate = fund.get("div_rate", "N/A")
        payout_ratio = fund.get("payout_ratio", 0.0)
        tot_div_recv = total_received.get(sym, 0)
        history_years = distinct_years.get(sym, 0)
        
        # Fundamental Quality
        eps = fund.get("eps") or 0.0
        roe = fund.get("roe") or 0.0
        roa = fund.get("roa") or 0.0
        debt_eq = fund.get("debt_eq") or 0.0
        fcf = fund.get("fcf") or 0.0
        ocf = fund.get("ocf") or 0.0
        
        # Valuation
        pe = fund.get("pe", 0.0)
        pb = fund.get("pb", 0.0)
        
        # Scoring
        
        # 1. Quality Score (0-100)
        q_score = 0
        if roe > 15: q_score += 25
        elif roe >= 10: q_score += 15
        
        if roa > 5: q_score += 25
        elif roa >= 2: q_score += 15
        
        if fcf > 0 and ocf > 0: q_score += 25
        elif ocf > 0: q_score += 10
        
        if history_years > 3: q_score += 25
        elif history_years == 2: q_score += 15
        elif history_years == 1: q_score += 5
        
        # 2. Safety Score (0-100)
        s_score = 0
        if payout_ratio > 0 and payout_ratio < 60: s_score += 40
        elif payout_ratio >= 60 and payout_ratio <= 80: s_score += 20
        
        if debt_eq < 0.5: s_score += 40
        elif debt_eq <= 1.0: s_score += 20
        
        if eps > 0: s_score += 20
        
        # 3. Valuation Score (0-100)
        v_score = 50 # Default fair
        if current_yield > 0 and hist_yield_5y > 0:
            ratio = current_yield / hist_yield_5y
            if ratio > 1.2: v_score = 100
            elif ratio > 1.0: v_score = 75
            elif ratio > 0.8: v_score = 50
            else: v_score = 25
            
        # 4. Overall Score (0-100)
        yield_score = min((current_yield / 8.0) * 100, 100)
        overall = int((s_score * 0.40) + (q_score * 0.30) + (v_score * 0.20) + (yield_score * 0.10))
        
        # Risk & Trap
        trap_risk = "LOW"
        if (current_yield > 8 and payout_ratio > 100) or \
           (current_yield > 6 and fcf < 0 and debt_eq > 1.0) or \
           (current_yield > 6 and eps < 0):
            trap_risk = "HIGH"
        elif payout_ratio > 80 or fcf < 0:
            trap_risk = "MEDIUM"
            
        # Buy Zone
        if overall > 75 and trap_risk == "LOW" and v_score >= 75:
            buy_zone = "ATTRACTIVE"
            decision = "STRONG BUY"
            reason = "High overall score, low risk, and attractive valuation."
        elif overall >= 60 and trap_risk != "HIGH":
            if v_score <= 50:
                buy_zone = "EXPENSIVE"
                decision = "HOLD"
                reason = "Good fundamentals but currently expensive."
            else:
                buy_zone = "FAIR"
                decision = "BUY"
                reason = "Solid fundamentals at a fair valuation."
        elif trap_risk == "HIGH" or overall < 50:
            buy_zone = "AVOID"
            decision = "AVOID"
            reason = "High dividend trap risk or poor fundamentals."
        else:
            buy_zone = "FAIR"
            decision = "WATCH"
            reason = "Average fundamentals. Monitor."
            
        row = [
            sym, sector, industry,
            current_yield, hist_yield_5y, div_rate, payout_ratio, history_years, tot_div_recv,
            eps, roe, roa, debt_eq, fcf, ocf,
            cmp, pe, pb,
            trap_risk,
            q_score, s_score, v_score, overall,
            buy_zone, decision, reason
        ]
        
        # Replace N/A or None with 0 or empty for sheets
        clean_row = [x if pd.notna(x) and x is not None else "" for x in row]
        rows.append(clean_row)
        
    # Sort by overall score descending
    rows.sort(key=lambda x: x[22] if isinstance(x[22], (int, float)) else 0, reverse=True)
    
    headers = [
        "Symbol", "Sector", "Industry",
        "Current Yield %", "5Y Hist Yield %", "Annual Div Rate", "Payout Ratio %", "Div History (Yrs)", "Total Div Recv (₹)",
        "EPS", "ROE %", "ROA %", "Debt/Eq", "FCF", "OCF",
        "CMP", "PE", "P/B",
        "Trap Risk",
        "Quality Score", "Safety Score", "Valuation Score", "Overall Score",
        "Buy Zone", "Decision", "Reason"
    ]
    
    return headers, rows


def write_dividends_tab(sh, headers, rows):
    import time
    import gspread.exceptions as _gse
    from sheet_writer import batch_update_safe
    from sheet_formatter import get_structural_format_reqs, color_cell_req, get_currency_format_reqs, get_percentage_format_reqs

    tab_name = "Dividends"
    num_cols = len(headers)
    
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

    widths = [70] * num_cols
    # Adjust some widths based on index
    widths[headers.index("Reason")] = 250
    widths[headers.index("Industry")] = 120
    
    data = [headers]
    if rows:
        data.extend(rows)

    for _attempt in range(5):
        try:
            ws.update(data)
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 4:
                time.sleep(15 * (2 ** _attempt))
            else:
                raise

    ws_id = ws.id
    reqs = get_structural_format_reqs(ws_id, len(data), num_cols, widths, freeze_rows=1, freeze_cols=1)

    # Format Specific Columns
    # Example logic for Conditional formatting using color_cell_req
    def apply_color(rn, c_idx, val, rule_type):
        if val == "": return None
        try:
            v = float(val)
        except:
            v = val
            
        bg, fg = None, None
        if rule_type == "score":
            if v >= 80: bg, fg = "00c853", "ffffff" # Strong Green
            elif v >= 65: bg, fg = "0b8043", "ffffff" # Green
            elif v >= 50: bg, fg = "fff2cc", "7f4f00" # Yellow
            elif v >= 35: bg, fg = "fde9d9", "c62828" # Light Red
            else: bg, fg = "cc0000", "ffffff" # Strong Red
        elif rule_type == "trap":
            if v == "LOW": bg, fg = "0b8043", "ffffff"
            elif v == "MEDIUM": bg, fg = "fff2cc", "7f4f00"
            elif v == "HIGH": bg, fg = "cc0000", "ffffff"
        elif rule_type == "buy_zone":
            if v == "ATTRACTIVE": bg, fg = "00c853", "ffffff"
            elif v == "FAIR": bg, fg = "0b8043", "ffffff"
            elif v == "EXPENSIVE": bg, fg = "fff2cc", "7f4f00"
            elif v == "AVOID": bg, fg = "cc0000", "ffffff"
        elif rule_type == "decision":
            if v == "STRONG BUY": bg, fg = "00c853", "ffffff"
            elif v == "BUY": bg, fg = "0b8043", "ffffff"
            elif v == "HOLD": bg, fg = "fff2cc", "7f4f00"
            elif v == "WATCH": bg, fg = "fce8b2", "7f4f00"
            elif v == "AVOID": bg, fg = "cc0000", "ffffff"
            
        if bg and fg:
            return color_cell_req(ws_id, rn, c_idx, bg, fg, bold=True)
        return None

    for i, r in enumerate(rows):
        rn = i + 1
        
        # Add color logic based on header position
        trap_idx = headers.index("Trap Risk")
        reqs.append(apply_color(rn, trap_idx, r[trap_idx], "trap"))
        
        for score_col in ["Quality Score", "Safety Score", "Valuation Score", "Overall Score"]:
            idx = headers.index(score_col)
            reqs.append(apply_color(rn, idx, r[idx], "score"))
            
        buy_idx = headers.index("Buy Zone")
        reqs.append(apply_color(rn, buy_idx, r[buy_idx], "buy_zone"))
        
        dec_idx = headers.index("Decision")
        reqs.append(apply_color(rn, dec_idx, r[dec_idx], "decision"))
        
    reqs = [r for r in reqs if r is not None]
    
    # Run batch update
    batch_update_safe(sh, reqs)
    log.info(f"Wrote {len(rows)} rows to {tab_name} tab.")

