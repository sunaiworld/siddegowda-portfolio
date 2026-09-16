"""
mf_stock_overlap.py
──────────────────────────────────────────────────────────────────────────────
Identifies underlying stock overlap across My Mutual Funds (Zerodha - Self).

Objectives:
- Extract underlying stock holdings of each mutual fund in my portfolio.
- Match stocks using canonical identifiers (ISIN > Symbol > Normalized Name).
- Count how many of my mutual funds hold each stock.
- List holding mutual funds and individual stock weights per fund.
- Calculate effective combined portfolio exposure (fund allocation × stock weight).
- Keep Dad's (Groww) and Wife's mutual funds strictly excluded.
"""

import os
import re
import csv
import glob
import json
import logging
from datetime import datetime, date

import sheet_formatter

log = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "mf_underlying_stocks_cache.json")


def clean_stock_symbol(sym: str) -> str:
    """Strip exchange suffixes like .NS and .BO from stock symbols."""
    s = str(sym).strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        s = s[:-3]
    return s


def normalize_company_name(name: str) -> str:
    """
    Produce a normalized company name for fallback matching.
    Strips company suffixes (Ltd, Limited, Ordinary Shares, Corp, India, etc.)
    and special characters.
    """
    if not name:
        return ""
    n = str(name).lower()
    n = re.sub(r"\b(ltd|limited|ordinary|shares|corp|corporation|inc|incorporated|india|pvt|co)\b", "", n)
    n = re.sub(r"[^a-z0-9]", "", n)
    return n


def load_known_stock_isins(imports_dir: str = "data/imports") -> dict:
    """
    Build a symbol -> ISIN lookup from local stock tradebooks and transactions.
    """
    isin_map = {}
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    search_root = os.path.join(here, imports_dir) if not os.path.isabs(imports_dir) else imports_dir
    
    if os.path.isdir(search_root):
        for path in glob.glob(os.path.join(search_root, "**", "*.csv"), recursive=True):
            try:
                with open(path, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        sym = str(r.get("symbol", "")).strip().upper()
                        isin = str(r.get("isin", "")).strip().upper()
                        if sym and isin and isin.startswith("INE"):
                            isin_map[sym] = isin
            except Exception:
                pass
    return isin_map


def _load_cache() -> dict:
    """Load cached mutual fund stock holdings."""
    if os.path.isfile(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Could not read MF stock holdings cache: {e}")
    return {}


def _save_cache(cache_data: dict):
    """Save mutual fund stock holdings to cache."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
    except Exception as e:
        log.warning(f"Could not save MF stock holdings cache: {e}")


def fetch_fund_underlying_stocks(isin: str, fund_name: str = "", use_cache: bool = True) -> list:
    """
    Fetches the underlying top equity holdings for a mutual fund given its ISIN.
    Uses Yahoo Finance (yfinance) Ticker.funds_data.top_holdings.
    Returns a list of dicts:
        [{
            "raw_symbol": str,
            "clean_symbol": str,
            "name": str,
            "weight": float,  # e.g. 0.054 (5.4%)
            "isin": str
        }, ...]
    """
    if not isin:
        return []

    cache = _load_cache() if use_cache else {}
    if use_cache and isin in cache:
        log.debug(f"Loaded underlying stocks from cache for {fund_name or isin}")
        return cache[isin].get("holdings", [])

    holdings_list = []
    try:
        import yfinance as yf
        s = yf.Search(isin, max_results=2)
        sym = s.quotes[0]["symbol"] if (s and s.quotes) else None
        if sym:
            t = yf.Ticker(sym)
            fd = getattr(t, "funds_data", None)
            th = getattr(fd, "top_holdings", None) if fd else None
            if th is not None and not th.empty:
                known_isins = load_known_stock_isins()
                for raw_sym, row in th.iterrows():
                    c_sym = clean_stock_symbol(raw_sym)
                    c_name = str(row.get("Name", "")).strip()
                    c_weight = float(row.get("Holding Percent", 0) or 0)
                    stk_isin = known_isins.get(c_sym, "")
                    
                    holdings_list.append({
                        "raw_symbol": str(raw_sym),
                        "clean_symbol": c_sym,
                        "name": c_name,
                        "weight": c_weight,
                        "isin": stk_isin
                    })
                
                # Cache results
                cache[isin] = {
                    "fund_name": fund_name,
                    "yf_symbol": sym,
                    "updated_at": datetime.now().isoformat(),
                    "holdings": holdings_list
                }
                _save_cache(cache)
                log.info(f"Fetched {len(holdings_list)} underlying stocks for {fund_name or isin} via {sym}")
    except Exception as e:
        log.warning(f"Failed to fetch underlying stocks for {fund_name or isin}: {e}")

    return holdings_list


def compute_mf_stock_overlap(holdings: dict, fund_stocks_map: dict = None, use_cache: bool = True) -> list:
    """
    Computes stock overlap across My Mutual Funds (Zerodha - Self).
    Strictly excludes Dad's (Groww) and Wife's holdings.

    Parameters:
    -----------
    holdings : dict
        Dict of all computed mutual fund holdings keyed by 'broker:fund_identifier'.
    fund_stocks_map : dict, optional
        Pre-supplied map of fund_name/isin -> list of stock dicts (useful for testing/mocking).
    use_cache : bool
        Whether to use local cached holdings.

    Returns:
    --------
    list of dict
        Sorted list of stock overlap records:
        [{
            "stock_name": str,
            "symbol": str,
            "isin": str,
            "overlap_count": int,
            "mutual_funds": list of str,
            "mutual_funds_str": str,
            "stock_weights": list of float,
            "stock_weights_str": str,
            "combined_exposure": float,
            "combined_exposure_pct": float
        }, ...]
    """
    # 1. Strictly filter for MY mutual funds (Zerodha - Self)
    my_funds = {}
    for k, h in holdings.items():
        broker = str(h.get("broker", "")).strip().lower()
        if broker == "zerodha":
            my_funds[k] = h

    if not my_funds:
        log.warning("No Zerodha (Self) mutual funds found for stock overlap analysis")
        return []

    total_my_val = sum(float(h.get("total_invested", 0) or 0) for h in my_funds.values())

    # 2. Extract underlying stocks for each fund in my portfolio
    known_isins = load_known_stock_isins()
    fund_holdings = {}

    for k, h in my_funds.items():
        isin = h.get("isin", "")
        fname = h.get("fund_name", "")
        inv = float(h.get("total_invested", 0) or 0)
        alloc_pct = (inv / total_my_val) if total_my_val > 0 else 0.0

        if fund_stocks_map is not None:
            # Used for testing or custom injection
            raw_stocks = fund_stocks_map.get(fname, fund_stocks_map.get(isin, []))
        else:
            raw_stocks = fetch_fund_underlying_stocks(isin, fund_name=fname, use_cache=use_cache)

        if raw_stocks:
            fund_holdings[fname] = {
                "isin": isin,
                "invested": inv,
                "allocation": alloc_pct,
                "stocks": raw_stocks
            }

    # 3. Group by canonical stock identifier
    # Priority: ISIN > Clean Ticker Symbol (alphanumeric) > Normalized Company Name
    stock_map = {}

    for fname, fdata in fund_holdings.items():
        alloc = fdata["allocation"]
        for stk in fdata["stocks"]:
            raw_sym = stk.get("raw_symbol", "")
            c_sym = clean_stock_symbol(stk.get("clean_symbol", raw_sym))
            name = str(stk.get("name", "")).strip() or c_sym
            weight = float(stk.get("weight", 0) or 0)
            isin = str(stk.get("isin", "")).strip()
            
            if not isin and c_sym in known_isins:
                isin = known_isins[c_sym]

            norm_name = normalize_company_name(name)

            # Determine grouping key
            if isin and isin.startswith("IN"):
                key = f"ISIN:{isin}"
            elif c_sym and not c_sym.isdigit():
                key = f"SYM:{c_sym}"
            elif norm_name:
                key = f"NAME:{norm_name}"
            else:
                key = f"RAW:{raw_sym or c_sym}"

            if key not in stock_map:
                stock_map[key] = {
                    "stock_name": name,
                    "symbol": c_sym,
                    "isin": isin,
                    "norm_name": norm_name,
                    "funds": [],
                    "fund_weights": [],  # (fund_name, weight)
                    "combined_exposure": 0.0
                }

            # Refine display name / isin / symbol if a better one appears
            curr_entry = stock_map[key]
            if len(name) > len(curr_entry["stock_name"]):
                curr_entry["stock_name"] = name
            if isin and not curr_entry["isin"]:
                curr_entry["isin"] = isin
            if not curr_entry["symbol"].isalpha() and c_sym.isalpha():
                curr_entry["symbol"] = c_sym

            curr_entry["funds"].append(fname)
            curr_entry["fund_weights"].append((fname, weight))
            curr_entry["combined_exposure"] += (alloc * weight)

    # 4. Format final list of records
    results = []
    for key, s in stock_map.items():
        overlap_count = len(s["funds"])
        
        # Build clean mutual funds string
        funds_str = " | ".join(s["funds"])
        
        # Build individual weights string: e.g. "Quant Flexi: 6.4% | Helios: 4.2%"
        weights_parts = []
        for fn, wt in s["fund_weights"]:
            # Shorten fund name for readability
            short_fn = fn.replace(" - Direct Plan", "").replace(" Direct Plan", "").replace(" Direct Growth", "").replace("-Direct Plan-Growth", "").strip()
            weights_parts.append(f"{short_fn}: {wt * 100:.1f}%")
        weights_str = " | ".join(weights_parts)

        eff_exposure_pct = round(s["combined_exposure"] * 100, 2)

        results.append({
            "stock_name": s["stock_name"],
            "symbol": s["symbol"],
            "isin": s["isin"],
            "overlap_count": overlap_count,
            "mutual_funds": s["funds"],
            "mutual_funds_str": funds_str,
            "stock_weights": [w for _, w in s["fund_weights"]],
            "stock_weights_str": weights_str,
            "combined_exposure": round(s["combined_exposure"], 4),
            "combined_exposure_pct": eff_exposure_pct
        })

    # Sort primarily by overlap count (descending), then by combined exposure (descending)
    results.sort(key=lambda r: (r["overlap_count"], r["combined_exposure"]), reverse=True)
    log.info(f"compute_mf_stock_overlap: found {len(results)} unique stocks ({sum(1 for r in results if r['overlap_count'] > 1)} overlapping)")
    return results


def build_stock_overlap_table_rows(stock_results: list, num_cols: int = 30) -> list:
    """
    Constructs the spreadsheet rows for the underlying stock overlap section.
    """
    rows = []
    
    # 1. Spacer row
    rows.append([""] * num_cols)

    # 2. Section Banner
    banner = ["UNDERLYING STOCK OVERLAP (MY MUTUAL FUNDS - ZERODHA)"] + [""] * (num_cols - 1)
    rows.append(banner)

    # 3. Table Column Headers
    headers = [
        "Stock",
        "ISIN",
        "Underlying Stock Overlap Count",
        "Underlying Mutual Funds",
        "Stock Weight / MF",
        "Combined MF Exposure",
    ] + [""] * (num_cols - 6)
    rows.append(headers)

    # 4. Data Rows
    for s in stock_results:
        row = [
            s["stock_name"],
            s["isin"],
            s["overlap_count"],
            s["mutual_funds_str"],
            s["stock_weights_str"],
            s["combined_exposure_pct"] / 100.0 if s["combined_exposure_pct"] else 0.0,
        ] + [""] * (num_cols - 6)
        rows.append(row)

    return rows


def get_stock_overlap_format_reqs(
    ws_id: int,
    banner_row_idx: int,
    table_header_row_idx: int,
    data_start_row_idx: int,
    stock_results: list,
    num_cols: int = 30
) -> list:
    """
    Generates Google Sheets formatting requests for the Stock Overlap section.
    """
    reqs = []
    total_data_rows = len(stock_results)

    # 1. Banner format (same dark blue banner style as sheet headers)
    reqs.append({
        "repeatCell": {
            "range": {
                "sheetId": ws_id,
                "startRowIndex": banner_row_idx, "endRowIndex": banner_row_idx + 1,
                "startColumnIndex": 0, "endColumnIndex": num_cols
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
            "range": {"sheetId": ws_id, "dimension": "ROWS", "startIndex": banner_row_idx, "endIndex": banner_row_idx + 1},
            "properties": {"pixelSize": 30}, "fields": "pixelSize"
        }
    })

    # 2. Table Headers format (dark blue/grey)
    reqs.append({
        "repeatCell": {
            "range": {
                "sheetId": ws_id,
                "startRowIndex": table_header_row_idx, "endRowIndex": table_header_row_idx + 1,
                "startColumnIndex": 0, "endColumnIndex": num_cols
            },
            "cell": {"userEnteredFormat": {
                "backgroundColor": sheet_formatter.hex_rgb("1c3144"),
                "textFormat": {"foregroundColor": sheet_formatter.hex_rgb("ffffff"), "bold": True, "fontSize": 8},
                "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE"
            }},
            "fields": "userEnteredFormat"
        }
    })

    if total_data_rows == 0:
        return reqs

    data_end_row_idx = data_start_row_idx + total_data_rows

    # 3. Center alignment for Count column (col 2)
    reqs.append({
        "repeatCell": {
            "range": {
                "sheetId": ws_id,
                "startRowIndex": data_start_row_idx, "endRowIndex": data_end_row_idx,
                "startColumnIndex": 2, "endColumnIndex": 3
            },
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"
        }
    })

    # 4. Percentage format for Combined MF Exposure (col 5)
    reqs += sheet_formatter.get_percentage_format_reqs(ws_id, data_start_row_idx, data_end_row_idx, 5, 6)

    # 5. Row-level styling for Overlap Count (amber for count > 1)
    for idx, s in enumerate(stock_results):
        r_idx = data_start_row_idx + idx
        count = s["overlap_count"]
        if count > 1:
            reqs.append(sheet_formatter.color_cell_req(ws_id, r_idx, 2, "fff2cc", "7f4f00", bold=True))
        else:
            reqs.append(sheet_formatter.color_cell_req(ws_id, r_idx, 2, "e8eaf6", "3949ab", bold=False))

    return reqs
