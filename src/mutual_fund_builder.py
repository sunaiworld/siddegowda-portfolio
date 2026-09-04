"""
mutual_fund_builder.py
──────────────────────────────────────────────────────────────────────────────
Mutual Fund pipeline — mirrors portfolio_builder.py for the "Mutual Funds" tab.

Pipeline:
    data/imports/mutual_funds/*.csv         Zerodha MF tradebooks
    data/imports/groww/Mutual_Funds_*.xlsx  Groww MF order history
           down
    load_all_mf_trades()
           down
    compute_mf_holdings()  per-fund holdings (all lots retained, FIFO sells)
           down
    compute_tax_harvest()  LTCG/STCG analysis per fund
           down
    write_mutual_funds()   updates "Mutual Funds" sheet
                           (existing 13 columns untouched, 6 new columns appended)

Tax rules (India equity MF):
    Held > 365 days: LTCG  (exempt up to Rs.1.25L per FY, taxed @12.5% above)
    Held <= 365 days: STCG  (taxed @20%)
"""

import os
import re
import glob
import logging
import importlib.util
from datetime import datetime, date, timedelta

import gspread

import sheet_formatter
import sheet_writer
from config import *
import mf_data_fetcher
import mf_analyzer

log = logging.getLogger(__name__)

# Tax constants
LTCG_EXEMPTION_LIMIT  = 125_000   # Rs.1.25L per FY (Budget 2024)
LTCG_BOOKED_THIS_FY   = 0         # Update if gains already realised this FY
EQUITY_MF_LTCG_DAYS   = 365       # >365 days held = LTCG for equity MFs


def _resolve_imports_root(imports_dir="data/imports"):
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, "..", imports_dir)
    if os.path.isdir(candidate):
        return os.path.normpath(candidate)
    if os.path.isdir(imports_dir):
        return os.path.normpath(imports_dir)
    raise FileNotFoundError(f"Cannot locate imports root: {imports_dir}")


def _load_importer(module_name, filepath):
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _today():
    return date.today()


def _parse_date(d):
    if isinstance(d, date):
        return d
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _normalise_fund_name(name):
    return re.sub(r"\s+", " ", name.lower().strip())


def _infer_category(name):
    n = name.upper()
    if "SMALL CAP" in n:                     return "Small Cap"
    if "MID CAP" in n or "MIDCAP" in n:      return "Mid Cap"
    if "LARGE CAP" in n or "BLUECHIP" in n:  return "Large Cap"
    if "FLEXI" in n:                          return "Flexi Cap"
    if "INFRA" in n:                          return "Sectoral/Thematic"
    if "BFSI" in n or "BANKING" in n:         return "Sectoral/Thematic"
    if "DIGITAL" in n or "PSU" in n:          return "Sectoral/Thematic"
    if "HYBRID" in n:                         return "Hybrid"
    if "DEBT" in n or "LIQUID" in n:          return "Debt"
    return "Other"


# LOAD ALL MF TRADES

def load_all_mf_trades(imports_dir="data/imports"):
    root      = _resolve_imports_root(imports_dir)
    mf_dir    = os.path.join(root, "mutual_funds")
    groww_dir = os.path.join(root, "groww")
    trades    = []

    if os.path.isdir(mf_dir):
        importer_path = os.path.join(mf_dir, "import_mf_zerodha.py")
        if os.path.isfile(importer_path):
            z_mod = _load_importer("_import_mf_zerodha", importer_path)
            # Zerodha MF tradebooks live in data/imports/zerodha/tradebook-*-MF_*.csv
            zerodha_dir = os.path.join(root, "zerodha")
            for path in sorted(glob.glob(os.path.join(zerodha_dir, "tradebook-*-MF_*.csv"))):
                try:
                    rows = z_mod.import_mf_zerodha(path)
                    trades.extend(rows)
                    log.info(f"  MF Zerodha {os.path.basename(path)}: {len(rows)} rows")
                except Exception as e:
                    log.warning(f"  MF Zerodha import failed for {os.path.basename(path)}: {e}")
        else:
            log.warning("import_mf_zerodha.py not found in mutual_funds/")

    if os.path.isdir(groww_dir):
        g_importer = os.path.join(mf_dir, "import_mf_groww.py") if os.path.isdir(mf_dir) else ""
        if g_importer and os.path.isfile(g_importer):
            g_mod = _load_importer("_import_mf_groww", g_importer)
            for path in sorted(glob.glob(os.path.join(groww_dir, "Mutual_Funds_*.xlsx"))):
                try:
                    rows = g_mod.import_mf_groww(path)
                    trades.extend(rows)
                    log.info(f"  MF Groww {os.path.basename(path)}: {len(rows)} rows")
                except Exception as e:
                    log.warning(f"  MF Groww import failed for {os.path.basename(path)}: {e}")
        else:
            log.warning("import_mf_groww.py not found")

    trades.sort(key=lambda t: str(t.get("date", "")))
    log.info(f"Loaded {len(trades)} total MF transactions")
    return trades


# COMPUTE MF HOLDINGS (FIFO)

def compute_mf_holdings(trades):
    raw_buckets = {}
    key_meta    = {}

    # Build ISIN→name lookup from trades that have an ISIN (Zerodha)
    isin_name_map = {}   # isin → canonical name (longest seen)
    for t in trades:
        isin = str(t.get("isin", "")).strip()
        name = str(t.get("fund_name", "")).strip()
        if isin:
            if isin not in isin_name_map or len(name) > len(isin_name_map[isin]):
                isin_name_map[isin] = name

    # Build reverse keyword→ISIN map for fuzzy matching no-ISIN trades (Groww)
    def _keywords(name):
        stop = {"fund", "direct", "plan", "growth", "the", "and", "&",
                "of", "a", "an", "regular", "idcw", "option"}
        words = re.sub(r"[^a-z0-9\s]", " ", name.lower()).split()
        return set(w for w in words if w not in stop and len(w) > 2)

    # First, build keyword map from our own Zerodha trades
    isin_keywords = {isin: _keywords(nm) for isin, nm in isin_name_map.items()}

    # Next, augment with AMFI data if available, to ensure we can map names to ISINs
    # even if Zerodha hasn't seen that fund yet.
    amfi_data = mf_data_fetcher.fetch_amfi_data()
    for isin, info in amfi_data.items():
        if isin not in isin_keywords:
            isin_keywords[isin] = _keywords(info["name"])

    def _resolve_isin(name):
        """Match a no-ISIN name to the best ISIN via keyword overlap."""
        query = _keywords(name)
        if not query:
            return None
        best_isin, best_score = None, 0
        for isin, kws in isin_keywords.items():
            score = len(query & kws)
            if score > best_score:
                best_isin, best_score = isin, score
        # Require at least 2 keyword overlaps to avoid false positives
        return best_isin if best_score >= 2 else None

    for t in trades:
        isin = str(t.get("isin", "")).strip()
        name = str(t.get("fund_name", "")).strip()
        broker = str(t.get("broker", "")).strip()
        if not isin:
            # Try to resolve via keyword match
            resolved = _resolve_isin(name)
            if resolved:
                isin = resolved
                log.debug(f"  Resolved no-ISIN '{name}' → ISIN {isin}")
        base_key  = isin if isin else _normalise_fund_name(name)
        key = f"{broker}:{base_key}"
        if key not in raw_buckets:
            raw_buckets[key] = []
            key_meta[key]    = {"fund_name": name, "isin": isin, "broker": broker}
        raw_buckets[key].append(t)
        if len(name) > len(key_meta[key]["fund_name"]):
            key_meta[key]["fund_name"] = name
        if isin and not key_meta[key]["isin"]:
            key_meta[key]["isin"] = isin


    holdings = {}
    for key, txns in raw_buckets.items():
        lots = []
        for t in txns:
            action = str(t.get("action", "")).strip().lower()
            units  = float(t.get("units", 0) or 0)
            nav    = float(t.get("nav",   0) or 0)
            amount = float(t.get("amount", units * nav))
            d      = _parse_date(t.get("date"))
            broker = t.get("broker", "")
            if units <= 0:
                continue
            if action == "buy":
                lots.append({"date": d, "units": units, "nav": nav,
                             "amount": amount, "broker": broker})
            elif action in ("sell", "redemption"):
                remaining = units
                new_lots  = []
                for lot in lots:
                    if remaining <= 0:
                        new_lots.append(lot)
                        continue
                    if lot["units"] <= remaining:
                        remaining -= lot["units"]
                    else:
                        frac = (lot["units"] - remaining) / lot["units"]
                        new_lots.append({"date": lot["date"],
                                         "units":  lot["units"] - remaining,
                                         "nav":    lot["nav"],
                                         "amount": lot["amount"] * frac,
                                         "broker": lot["broker"]})
                        remaining = 0
                lots = new_lots

        if not lots:
            continue

        total_units    = sum(l["units"]  for l in lots)
        total_invested = sum(l["amount"] for l in lots)
        avg_nav        = total_invested / total_units if total_units > 0 else 0

        holdings[key] = {
            "fund_name":      key_meta[key]["fund_name"],
            "isin":           key_meta[key]["isin"],
            "broker":         key_meta[key]["broker"],
            "category":       _infer_category(key_meta[key]["fund_name"]),
            "lots":           lots,
            "total_units":    round(total_units, 6),
            "total_invested": round(total_invested, 2),
            "avg_nav":        round(avg_nav, 4),
        }

    log.info(f"compute_mf_holdings: {len(holdings)} active funds")
    return holdings


# COMPUTE TAX HARVEST

def compute_tax_harvest(holdings, current_navs=None, ltcg_booked=None):
    if current_navs is None:
        current_navs = {}
    today            = _today()
    
    # Calculate dynamically booked LTCG this FY if not provided explicitly
    if ltcg_booked is None:
        # Determine current financial year start (April 1st)
        if today.month >= 4:
            fy_start = date(today.year, 4, 1)
        else:
            fy_start = date(today.year - 1, 4, 1)
        
        # We don't have historical sell tracking easily accessible here for realized LTCG.
        # A true dynamic system would store this in a "Realized Gains" tab. 
        # For now, default to the config value.
        ltcg_booked = LTCG_BOOKED_THIS_FY

    remaining_exempt = max(LTCG_EXEMPTION_LIMIT - ltcg_booked, 0)
    results          = {}

    amfi_data = mf_data_fetcher.fetch_amfi_data()

    for key, h in holdings.items():
        lots        = h["lots"]
        avg_nav     = h["avg_nav"]
        isin        = h["isin"]
        
        # Prefer user's sheet NAV, then AMFI live NAV, then fallback to avg_nav
        amfi_nav = amfi_data.get(isin, {}).get("nav", 0) if isin else 0
        curr_nav = float(current_navs.get(key, 0) or amfi_nav or avg_nav)
        
        oldest_date = None
        ltcg_units = ltcg_cost = ltcg_value = 0.0
        stcg_units = stcg_cost = stcg_value = 0.0

        for lot in lots:
            ld = lot["date"]
            if ld is None:
                continue
            days = (today - ld).days
            lv   = lot["units"] * curr_nav
            lc   = lot["amount"]
            if oldest_date is None or ld < oldest_date:
                oldest_date = ld
            if days > EQUITY_MF_LTCG_DAYS:
                ltcg_units += lot["units"]; ltcg_cost += lc; ltcg_value += lv
            else:
                stcg_units += lot["units"]; stcg_cost += lc; stcg_value += lv

        holding_days  = (today - oldest_date).days if oldest_date else 0
        total_value   = ltcg_value + stcg_value
        total_gain    = total_value - h["total_invested"]
        ltcg_gain     = max(ltcg_value - ltcg_cost, 0) if ltcg_units > 0 else 0.0
        harvestable   = min(ltcg_gain, remaining_exempt)
        
        harvestable_units = (harvestable / curr_nav) if curr_nav > 0 and harvestable > 0 else 0.0

        if ltcg_units > 0 and stcg_units > 0:
            tax_type = "Mixed"
        elif ltcg_units > 0:
            tax_type = "LTCG"
        else:
            tax_type = "STCG"

        if ltcg_gain >= 1000 and harvestable >= 500 and remaining_exempt > 0:
            recommendation = "YES"
            reason = f"LTCG Rs.{ltcg_gain:,.0f} eligible; harvest Rs.{harvestable:,.0f} within exemption"
        elif ltcg_gain < 0 and abs(ltcg_gain) >= 1000:
            recommendation = "YES"
            reason = f"Long-term loss of Rs.{abs(ltcg_gain):,.0f} available to offset gains"
        elif tax_type == "Mixed":
            recommendation = "REVIEW"
            reason = "Mixed STCG/LTCG lots"
        elif tax_type == "STCG":
            recommendation = "NO"
            reason = "All lots STCG"
        elif ltcg_gain < 1000 and ltcg_gain >= 0:
            recommendation = "NO"
            reason = "LTCG gain too small to harvest"
        elif remaining_exempt <= 0:
            recommendation = "NO"
            reason = "LTCG exemption fully used this FY"
        else:
            recommendation = "NO"
            reason = ""

        results[key] = {
            "holding_days":    holding_days,
            "tax_type":        tax_type,
            "current_value":   round(total_value, 2),
            "unrealised_gain": round(total_gain, 2),
            "ltcg_gain":       round(ltcg_gain, 2),
            "harvestable":     round(harvestable, 2),
            "ltcg_units":      round(ltcg_units, 3),
            "harvest_units":   round(harvestable_units, 3),
            "recommendation":  recommendation,
            "reason":          reason,
            "amfi_nav":        amfi_nav
        }

    log.info(f"compute_tax_harvest: {len(results)} funds analysed")
    return results


# WRITE MUTUAL FUNDS SHEET

_EXISTING_HEADERS = [
    "Fund Name", "Category", "Avg Buy NAV", "Current NAV",
    "Units", "Invested", "Current Value", "P&L", "Return%",
    "Day Gain Rs", "Day Gain%", "Weight%", "Signal",
]
_NEW_HEADERS = [
    "Holding Days", "Tax Type", "Unrealised Gain",
    "Harvestable", "LTCG Units", "Harvestable Units",
    "Tax Harvesting", "Reason",
]
_DECISION_HEADERS = [
    "1Y Ret%", "3Y Ret%", "5Y Ret%",
    "MF Score", "Trend", "AI Decision", "Decision Reason",
]
_ALL_HEADERS = _EXISTING_HEADERS + _NEW_HEADERS + _DECISION_HEADERS


def write_mutual_funds(sh, holdings, tax_data, tab_name="Mutual Funds"):
    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        log.warning(f"'{tab_name}' sheet not found — creating")
        ws = sh.add_worksheet(title=tab_name, rows=200, cols=25)

    # Preserve user-entered Current NAV and Day Gain values from existing sheet
    existing = ws.get_all_values()
    existing_nav  = {}
    existing_gain = {}
    if len(existing) > 1:
        try:
            hdr = [str(c).strip() for c in existing[0]]
            fn_col  = 0
            nav_col = next((i for i, h in enumerate(hdr) if "Current NAV" in h), 3)
            dg_col  = next((i for i, h in enumerate(hdr) if "Day Gain" in h and "%" not in h), 9)
            dgp_col = next((i for i, h in enumerate(hdr) if "Day Gain" in h and "%" in h), 10)
            for row in existing[1:]:
                fn = str(row[fn_col]).strip() if len(row) > fn_col else ""
                if not fn or fn == "TOTAL":
                    continue
                try:
                    existing_nav[fn] = float(str(row[nav_col]).replace(",", "")) if len(row) > nav_col else 0
                except Exception:
                    pass
                try:
                    dg  = float(str(row[dg_col]).replace(",", ""))  if len(row) > dg_col  else 0
                    dgp = float(str(row[dgp_col]).replace(",", "")) if len(row) > dgp_col else 0
                    existing_gain[fn] = [dg, dgp]
                except Exception:
                    existing_gain[fn] = [0, 0]
        except Exception as e:
            log.warning(f"Could not read existing MF values: {e}")

    groww_rows = []
    zerodha_rows = []
    total_invested_g = 0.0
    total_value_g = 0.0
    total_invested_z = 0.0
    total_value_z = 0.0

    sorted_keys = sorted(holdings.keys(), key=lambda k: holdings[k]["total_invested"], reverse=True)

    for key in sorted_keys:
        h      = holdings[key]
        tx     = tax_data.get(key, {})
        fn     = h["fund_name"]
        cat    = h["category"]
        avg_nav   = h["avg_nav"]
        units     = h["total_units"]
        invested  = h["total_invested"]
        broker    = h.get("broker", "")

        curr_nav  = existing_nav.get(fn, 0) or tx.get("amfi_nav", 0) or avg_nav
        curr_val  = round(units * curr_nav, 2)
        pnl       = round(curr_val - invested, 2)
        ret_pct   = round((pnl / invested) * 100, 2) if invested else 0
        dg, dgp   = existing_gain.get(fn, [0, 0])

        if   ret_pct >= 100: signal = "STAR"
        elif ret_pct >= 50:  signal = "MULTI"
        elif ret_pct >= 20:  signal = "PROFIT"
        elif ret_pct >= 0:   signal = "HOLD"
        elif ret_pct >= -10: signal = "REVIEW"
        else:                signal = "EXIT"

        wt = 0.0

        # AI Decision Support
        isin = h.get("isin")
        analysis = mf_analyzer.analyze_fund(isin) if isin else None
        if analysis:
            ret_1y = f"{analysis['ret_1y']*100:.2f}%" if analysis['ret_1y'] is not None else ""
            ret_3y = f"{analysis['ret_3y']*100:.2f}%" if analysis['ret_3y'] is not None else ""
            ret_5y = f"{analysis['ret_5y']*100:.2f}%" if analysis['ret_5y'] is not None else ""
            mf_score = round(analysis['overall_score']) if analysis['overall_score'] is not None else ""
            trend = analysis['trend']
            ai_decision = analysis['decision']
            ai_reason = analysis['reason']
        else:
            ret_1y = ret_3y = ret_5y = mf_score = trend = ai_decision = ai_reason = ""

        row_dict = {
            "fn": fn, "cat": cat, "avg_nav": avg_nav, "curr_nav": curr_nav,
            "units": round(units, 3), "invested": invested, "curr_val": curr_val,
            "pnl": pnl, "ret_pct": ret_pct, "dg": dg, "dgp": dgp, "wt": wt,
            "signal": signal,
            "holding_days": tx.get("holding_days", ""),
            "tax_type":     tx.get("tax_type", ""),
            "unrealised":   tx.get("unrealised_gain", ""),
            "harvestable":  tx.get("harvestable", ""),
            "ltcg_units":   tx.get("ltcg_units", ""),
            "harvest_units": tx.get("harvest_units", ""),
            "rec":          tx.get("recommendation", ""),
            "reason":       tx.get("reason", ""),
            "ret_1y": ret_1y, "ret_3y": ret_3y, "ret_5y": ret_5y,
            "mf_score": mf_score, "trend": trend, "ai_decision": ai_decision, "ai_reason": ai_reason
        }

        if broker.lower() == "groww":
            groww_rows.append(row_dict)
            total_invested_g += invested
            total_value_g += curr_val
        else:
            zerodha_rows.append(row_dict)
            total_invested_z += invested
            total_value_z += curr_val

    # Recalculate weight%
    for r in groww_rows:
        r["wt"] = round((r["curr_val"] / total_value_g) * 100, 2) if total_value_g else 0
    for r in zerodha_rows:
        r["wt"] = round((r["curr_val"] / total_value_z) * 100, 2) if total_value_z else 0

    all_data = [_ALL_HEADERS]
    
    header_indices = []
    subtotal_indices = []

    def _add_section(title, rows, tot_inv, tot_val):
        header_indices.append(len(all_data))
        all_data.append([title] + [""] * (len(_ALL_HEADERS) - 1))
        
        for r in rows:
            all_data.append([
                r["fn"], r["cat"], r["avg_nav"], r["curr_nav"],
                r["units"], r["invested"], r["curr_val"], r["pnl"], r["ret_pct"],
                r["dg"], r["dgp"], r["wt"], r["signal"],
                r["holding_days"], r["tax_type"], r["unrealised"], r["harvestable"],
                r["ltcg_units"], r["harvest_units"],
                r["rec"], r["reason"],
                r["ret_1y"], r["ret_3y"], r["ret_5y"],
                r["mf_score"], r["trend"], r["ai_decision"], r["ai_reason"]
            ])
            
        subtotal_indices.append(len(all_data))
        tpnl = round(tot_val - tot_inv, 2)
        tret = round((tpnl / tot_inv) * 100, 2) if tot_inv else 0
        all_data.append([
            f"{title} SUBTOTAL", "", "", "", "",
            round(tot_inv, 2), round(tot_val, 2), tpnl, tret,
            "", "", 100.0, "",
            "", "", tpnl, "", "", "", "", "",
            "", "", "", "", "", "", ""
        ])
        all_data.append([""] * len(_ALL_HEADERS))

    if groww_rows:
        _add_section("GROWW - DAD", groww_rows, total_invested_g, total_value_g)
    if zerodha_rows:
        _add_section("ZERODHA - SELF", zerodha_rows, total_invested_z, total_value_z)

    # TOTAL / TAX HARVESTING
    header_indices.append(len(all_data))
    all_data.append(["TOTAL / TAX HARVESTING"] + [""] * (len(_ALL_HEADERS) - 1))
    
    subtotal_indices.append(len(all_data))
    total_invested = total_invested_g + total_invested_z
    total_value = total_value_g + total_value_z
    tot_pnl = round(total_value - total_invested, 2)
    tot_ret = round((tot_pnl / total_invested) * 100, 2) if total_invested else 0
    all_data.append([
        "COMBINED TOTAL", "", "", "", "",
        round(total_invested, 2), round(total_value, 2), tot_pnl, tot_ret,
        "", "", 100.0, "",
        "", "", tot_pnl, "", "", "", "", "",
        "", "", "", "", "", "", ""
    ])

    sheet_writer.clear_sheet_safe(ws)
    sheet_writer.batch_update_safe(sh, sheet_formatter.clear_all_formatting_reqs(ws.id))
    sheet_writer.update_sheet_safe(ws, "A1", all_data, value_input_option="RAW")
    log.info(f"write_mutual_funds: wrote {len(all_data)} rows to '{tab_name}'")

    # Formatting
    nc = len(_ALL_HEADERS)
    # Compact column widths — GITHUB DATA compact philosophy
    # Fund Name(180), Category(80), Avg NAV(65), Curr NAV(65),
    # Units(60), Invested(85), Curr Val(85), P&L(80), Return%(65),
    # Day Gain Rs(75), Day Gain%(65), Weight%(55), Signal(75),
    # Holding Days(70), Tax Type(60), Unrealised(85), Harvestable(85),
    # LTCG Units(65), Harvest Units(70), Tax Harvest(65), Reason(170),
    # 1Y Ret%(60), 3Y Ret%(60), 5Y Ret%(60), MF Score(65),
    # Trend(80), AI Decision(90), Decision Reason(170)
    widths = [
        180, 80, 65, 65, 60, 85, 85, 80, 65, 75, 65, 55, 75,
        70, 60, 85, 85, 65, 70, 65, 170,
        60, 60, 60, 65, 80, 90, 170,
    ]
    reqs = sheet_formatter.get_structural_format_reqs(
        ws.id, len(all_data), nc, widths=widths, freeze_rows=1, freeze_cols=1)

    # Currency cols (0-indexed): AvgNAV(2), CurrNAV(3), Invested(5), CurrVal(6), PnL(7), DayGainRs(9), Unrealised(15), Harvestable(16)
    for col in [2, 3, 5, 6, 7, 9, 15, 16]:
        reqs += sheet_formatter.get_currency_format_reqs(ws.id, 1, len(all_data), col, col + 1)
    # Percent cols (0-indexed): Return%(8), DayGain%(10), Weight%(11)
    for col in [8, 10, 11]:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, 1, len(all_data), col, col + 1)
    # 3 decimal precision for Units (4), LTCG Units (17), Harvest Units (18)
    for col in [4, 17, 18]:
        reqs += [{"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": len(all_data), "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.000"}}},
            "fields": "userEnteredFormat.numberFormat"
        }}]
    # Percent cols for 1Y Ret%(21), 3Y Ret%(22), 5Y Ret%(23)
    for col in [21, 22, 23]:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, 1, len(all_data), col, col + 1)

    for i, row in enumerate(all_data):
        rn = i
        if i == 0 or len(row) <= 1 or row[0] == "" or "SUBTOTAL" in row[0] or "TOTAL" in row[0] or "GROWW" in row[0] or "ZERODHA" in row[0]:
            continue

        try:
            pnl = float(str(row[7]).replace("₹", "").replace(",", "").strip()) if row[7] else 0.0
            req = sheet_formatter.color_positive_negative(ws.id, rn, 7, pnl)
            if req: reqs.append(req)
        except: pass
        
        try:
            ret_pct = float(str(row[8]).replace("%", "").replace(",", "").strip()) if row[8] else 0.0
            req = sheet_formatter.color_positive_negative(ws.id, rn, 8, ret_pct)
            if req: reqs.append(req)
        except: pass

        # Signal column colour (col 12) — same colour_cell_req approach as GITHUB DATA
        sig = str(row[12])
        if   sig == "STAR":   reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "00c853", "ffffff"))
        elif sig == "MULTI":  reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "0b8043", "ffffff"))
        elif sig == "PROFIT": reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "d9ead3", "0b8043"))
        elif sig == "HOLD":   reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "fff2cc", "7f4f00"))
        elif sig == "REVIEW": reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "fce8b2", "7f4f00"))
        elif sig == "EXIT":   reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "cc0000", "ffffff"))

        rec = str(row[19])
        if   rec == "YES":    reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 19, "0f9d58", "ffffff"))
        elif rec == "REVIEW": reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 19, "fce8b2", "7f4f00"))
        elif rec == "NO":     reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 19, "e8eaf6", "3949ab"))
        
        tt = str(row[14])
        if   tt == "LTCG":  reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 14, "d9ead3", "0b8043", bold=False))
        elif tt == "STCG":  reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 14, "fde9d9", "c62828", bold=False))
        elif tt == "Mixed": reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 14, "fff2cc", "7f4f00", bold=False))

        ai_dec = str(row[26]) if len(row) > 26 else ""
        if   ai_dec == "BUY / ADD":       reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 26, "d9ead3", "0b8043", bold=True))
        elif ai_dec == "HOLD":            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 26, "e8eaf6", "3949ab", bold=True))
        elif ai_dec == "WATCH":           reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 26, "fff2cc", "f57f17", bold=True))
        elif ai_dec == "REVIEW / REDUCE": reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 26, "fce8b2", "d32f2f", bold=True))

        # 1Y / 3Y / 5Y Ret% (cols 21, 22, 23) — color_positive_negative (same as GITHUB DATA)
        for ret_col in [21, 22, 23]:
            try:
                ret_v = float(str(row[ret_col]).replace("%", "").replace(",", "").strip()) if len(row) > ret_col and row[ret_col] else 0.0
                req_r = sheet_formatter.color_positive_negative(ws.id, rn, ret_col, ret_v)
                if req_r: reqs.append(req_r)
            except: pass

        # MF Score (col 24) — same thresholds as GITHUB DATA Total Score
        try:
            mf_sc = float(row[24]) if len(row) > 24 and row[24] else None
            if mf_sc is not None:
                if   mf_sc >= 65: reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 24, "00c853", "ffffff"))
                elif mf_sc >= 50: reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 24, "d9ead3", "0b8043"))
                elif mf_sc >= 35: reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 24, "fff2cc", "7f4f00"))
                else:             reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 24, "fde9d9", "c62828"))
        except: pass

    # Style section headers as dark blue banners
    for h_idx in header_indices:
        reqs.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": h_idx, "endRowIndex": h_idx + 1,
                    "startColumnIndex": 0, "endColumnIndex": nc
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": sheet_formatter.hex_rgb("1f4e78"),
                    "textFormat": {"foregroundColor": sheet_formatter.hex_rgb("ffffff"), "bold": True, "fontSize": 9},
                    "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE"
                }},
                "fields": "userEnteredFormat"
            }
        })
        reqs.append({
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": h_idx, "endIndex": h_idx + 1},
                "properties": {"pixelSize": 30}, "fields": "pixelSize"
            }
        })

    # Style subtotal rows
    for s_idx in subtotal_indices:
        for col in range(nc):
            if col in (7, 8):  # PnL (col 7), Return % (col 8)
                try:
                    val = float(str(all_data[s_idx][col]).replace("₹", "").replace("%", "").replace(",", "").strip()) if all_data[s_idx][col] else 0.0
                    bg = "d9ead3" if val > 0 else "fde9d9" if val < 0 else "f1f1f1"
                    fg = "0b8043" if val > 0 else "c62828" if val < 0 else "666666"
                    reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx, col, bg, fg, bold=True, font_size=8))
                    continue
                except: pass
            reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx, col, "1c3144", "ffffff", font_size=8))

    # Filter over the full table
    reqs.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": len(all_data),
                    "startColumnIndex": 0,
                    "endColumnIndex": nc,
                }
            }
        }
    })

    sheet_writer.batch_update_safe(sh, reqs)
    log.info(f"write_mutual_funds: {len(reqs)} format requests applied")


# MAIN ENTRY POINT

def run_mutual_fund_update(sh, imports_dir="data/imports"):
    log.info("Mutual Fund update starting")
    trades   = load_all_mf_trades(imports_dir)
    if not trades:
        log.warning("No MF trades loaded — skipping")
        return
    holdings = compute_mf_holdings(trades)
    if not holdings:
        log.warning("No active MF holdings — skipping write")
        return
    tax_data = compute_tax_harvest(holdings)
    write_mutual_funds(sh, holdings, tax_data)
    log.info(f"Mutual Fund update complete: {len(holdings)} funds")
