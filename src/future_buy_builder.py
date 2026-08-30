import os
import json
import time
import logging
import statistics
import requests
import math
from datetime import datetime, date, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
import fund_cache

import portfolio_analytics

from news_engine.sources import google_news_rss
from news_engine import classifier

log = logging.getLogger("portfolio")

from config import *
from sheet_formatter import *
from github_data_builder import build_result_row
from score_engine import *
from data_fetcher import fetch_prices_batch, fetch_technicals, fetch_rev_growth



# ══════════════════════════════════════════════
# WATCHLIST PROCESSING (Future Buy, and any future
# watchlist added to WATCHLISTS). Market-data only —
# no qty, no buy price, no purchase date, no XIRR/SL/
# target alerts. Duplicate symbols vs Portfolio/GITHUB
# DATA are independent here and never touch holdings
# or portfolio-value calculations.
# ══════════════════════════════════════════════
def process_watchlist_tab(sh, tab_name, symbols, nc_cache=None, shared_prices=None, shared_fund=None, shared_tech=None, shared_rev=None, sector_weights=None, portfolio_value=None):
    if not symbols:
        log.warning(f"{tab_name}: no symbols configured, skipping")
        return []

    shared_prices = shared_prices or {}
    shared_fund = shared_fund or {}
    shared_tech = shared_tech or {}
    shared_rev = shared_rev or {}

    missing_syms = [s for s in symbols if s not in shared_prices]
    prices = shared_prices.copy()
    if missing_syms:
        log.info(f"{tab_name}: fetching prices for {len(missing_syms)} new symbols...")
        prices.update(fetch_prices_batch(missing_syms))

    wl_cache = fund_cache.load_cache(sh)
    
    missing_fund = [s for s in symbols if s not in shared_fund]
    fund_map = shared_fund.copy()
    if missing_fund:
        log.info(f"{tab_name}: fetching fundamentals for {len(missing_fund)} new symbols...")
        def _fetch_fund(sym):
            return sym, fund_cache.get_or_fetch_fundamentals(sym, wl_cache, max_age_days=FUNDAMENTALS_CACHE_DAYS)
        with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
            futures = {ex.submit(_fetch_fund, sym): sym for sym in missing_fund}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, data = fut.result()
                    fund_map[sym] = data
                except Exception as e:
                    fund_map[sym] = {}
        fund_cache.save_cache(sh, wl_cache)

    missing_tech = [s for s in symbols if s not in shared_tech]
    tech_map = shared_tech.copy()
    rev_map = shared_rev.copy()
    if missing_tech:
        log.info(f"{tab_name}: fetching tech/growth for {len(missing_tech)} new symbols...")
        def _fetch_tech(sym):
            return sym, fetch_technicals(sym), fetch_rev_growth(sym)
        with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
            futures = {ex.submit(_fetch_tech, sym): sym for sym in missing_tech}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    tech_map[sym] = tech
                    rev_map[sym] = rev
                    if tech.get("beta_nifty") is not None:
                        fund_map.setdefault(sym, {})["beta"] = tech["beta_nifty"]
                except Exception as e:
                    tech_map[sym] = {}
                    rev_map[sym] = None

    rows = []
    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            log.warning(f"  SKIP {sym} ({tab_name}) — no price")
            continue

        f = fund_map.get(sym, {})
        tech = tech_map.get(sym, {})
        rev_gr = rev_map.get(sym)

        nd = (nc_cache or {}).get(sym.upper(), {})

        row, archetype, tot_sc, final_action = build_result_row(
            sym, cmp, f, tech, rev_gr, xirr_val="", news_data=nd if nd else None
        )
        rows.append(row)
        news_tag = f" | News:{nd.get('sentiment','')}" if nd.get('sentiment') else ""
        log.info(f"  {sym:12} | {archetype:25} | Total:{tot_sc:3} | {final_action}{news_tag}")

    # Opportunity-first ordering: sort by Buying Zone priority, then Total Score descending
    ZONE_RANK = {
        "🟢🟢 ADD AGGRESSIVELY": 1,
        "🟢 ACCUMULATE": 2,
        "🟡 SMALL BUY": 3,
        "❌ WAIT": 4,
        "🔎 INVESTIGATE": 5,
    }
    from github_data_builder import GITHUB_DATA_COLS
    C_MAP = GITHUB_DATA_COLS
    def _watchlist_sort_key(r):
        zone_str = str(r[C_MAP["buying_zone"]]).strip() if len(r) > C_MAP["buying_zone"] else ""
        z_rank = ZONE_RANK.get(zone_str, 9)
        tot_val = 0.0
        if len(r) > C_MAP["total"] and r[C_MAP["total"]] != "":
            try:
                tot_val = float(r[C_MAP["total"]])
            except:
                pass
        return (z_rank, -tot_val)

    rows.sort(key=_watchlist_sort_key)

    write_future_buy_tab(sh, rows, tab_name=tab_name, sector_weights=sector_weights, portfolio_value=portfolio_value)
    return rows


def write_future_buy_tab(sh, rows, tab_name="Future Buy", sector_weights=None, portfolio_value=None):
    """
    Writes the Future Buy watchlist tab with:
    1. A compact Top 10 Buy Opportunities callout block at the top with Portfolio Fit & Tranche Guidance.
    2. A blank separator row.
    3. The complete, intact watchlist (111 stocks) below, ordered opportunity-first
       with full GITHUB DATA styling and group headers.
    """
    from github_data_builder import (
        GITHUB_DATA_COLS, GITHUB_DATA_HEADER_NAMES, GITHUB_DATA_COL_WIDTHS,
        GROUP_DEFS, clean_row
    )
    from sheet_writer import clear_sheet_safe, update_sheet_safe, batch_update_safe

    C = GITHUB_DATA_COLS
    num_cols = len(C)

    # 1. Top 10 Opportunities (selected from top of already-sorted opportunity-first rows)
    top10 = rows[:10]
    top10_headers = [
        "Rank", "Symbol", "Buying Zone", "Total Score", "CMP",
        "Buy/Sell Price Range", "Portfolio Fit", "Tranche Guidance", "Action"
    ]

    top10_title_row = ["TOP 10 BUY OPPORTUNITIES"] + [""] * (num_cols - 1)
    top10_hdr_row = top10_headers + [""] * (num_cols - len(top10_headers))
    top10_data_rows = []
    top10_fit_styles = []

    for rank, r in enumerate(top10, 1):
        r_clean = clean_row(r)
        sym = r_clean[C["symbol"]]
        sector = r_clean[C["sector"]] if "sector" in C and len(r_clean) > C["sector"] else ""

        # Portfolio Fit calculation
        if sector_weights and isinstance(sector_weights, dict) and sector:
            wt = float(sector_weights.get(sector, 0.0))
            if wt > 20.0:
                fit_text = f"⚠️ Overweight ({wt:.1f}%)"
                fit_style = ("fde9d9", "c62828")
            elif wt >= 15.0:
                fit_text = f"⚖️ Balanced ({wt:.1f}%)"
                fit_style = ("fff2cc", "7f4f00")
            else:
                fit_text = f"⭐ High Fit ({wt:.1f}%)" if wt > 0 else "⭐ High Fit (New)"
                fit_style = ("d9ead3", "0b8043")
        else:
            fit_text = "⭐ High Fit (Diversified)"
            fit_style = ("d9ead3", "0b8043")

        top10_fit_styles.append(fit_style)

        # Tranche sizing guidance (standard 2% tranche)
        if portfolio_value and portfolio_value > 0:
            tranche_val = round(portfolio_value * 0.02, -3)
            tranche_text = f"₹{tranche_val:,.0f} (2.0%)"
        else:
            tranche_text = "2.0% Tranche"

        top10_data_rows.append([
            rank,
            r_clean[C["symbol"]],
            r_clean[C["buying_zone"]],
            r_clean[C["total"]],
            r_clean[C["cmp"]],
            r_clean[C["price_range"]],
            fit_text,
            tranche_text,
            r_clean[C["action"]],
        ] + [""] * (num_cols - len(top10_headers)))

    blank_sep = [""] * num_cols

    # 2. Main Watchlist Table (full 41-column GITHUB DATA layout)
    headers = [""] * num_cols
    widths  = [70] * num_cols
    for key, idx in C.items():
        headers[idx] = GITHUB_DATA_HEADER_NAMES.get(key, key)
        widths[idx]  = GITHUB_DATA_COL_WIDTHS.get(key, 70)

    FROZEN_COLS = 1
    group_ranges = [(C[sk], C[ek], label) for sk, ek, label in GROUP_DEFS]
    group_row = [""] * num_cols
    for start_col, end_col, label in group_ranges:
        label_col = FROZEN_COLS if (start_col < FROZEN_COLS <= end_col) else start_col
        group_row[label_col] = label

    watchlist_rows = [clean_row(r) for r in rows]

    all_data = [top10_title_row, top10_hdr_row] + top10_data_rows + [blank_sep, group_row, headers] + watchlist_rows

    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(tab_name, rows=len(all_data) + 20, cols=num_cols)

    import sheet_writer

    try:
        sheet_writer.clear_sheet_safe(ws)
    except Exception as e:
        log.error(f"[write_future_buy_tab] watchlist='{tab_name}' tab='{tab_name}' stage='clear worksheet' failed ({type(e).__name__}): {e}")
        raise

    try:
        sheet_writer.batch_update_safe(sh, clear_all_formatting_reqs(ws.id))
    except Exception as e:
        log.error(f"[write_future_buy_tab] watchlist='{tab_name}' tab='{tab_name}' stage='clear formatting' failed ({type(e).__name__}): {e}")
        raise

    try:
        sheet_writer.update_sheet_safe(ws, "A1", all_data, value_input_option="USER_ENTERED")
    except Exception as e:
        log.error(f"[write_future_buy_tab] watchlist='{tab_name}' tab='{tab_name}' stage='write row data + headers' failed ({type(e).__name__}): {e}. "
                  f"Worksheet was already cleared — '{tab_name}' has no data/formatting until the next successful run.")
        raise

    reqs = []
    reqs.append({"clearBasicFilter": {"sheetId": ws.id}})

    # Top 10 Title Banner: Merge A1:I1, deep navy
    reqs.append({
        "mergeCells": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(top10_headers)},
            "mergeType": "MERGE_ALL"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(top10_headers)},
            "cell": {"userEnteredFormat": {
                "backgroundColor": hex_rgb("1a237e"),
                "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 11},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"
            }},
            "fields": "userEnteredFormat"
        }
    })

    # Top 10 Header Row (Row 2)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": len(top10_headers)},
            "cell": {"userEnteredFormat": {
                "backgroundColor": hex_rgb("37474f"),
                "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 10},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"
            }},
            "fields": "userEnteredFormat"
        }
    })

    # Top 10 Data Rows Formatting
    for idx, r in enumerate(top10):
        rn = idx + 2
        # Rank (Col 0)
        reqs.append({
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn + 1, "startColumnIndex": 0, "endColumnIndex": 1},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.horizontalAlignment"
            }
        })
        # Symbol (Col 1)
        reqs.append(color_cell_req(ws.id, rn, 1, "eaf4fb", "1565c0", bold=True))
        # Buying Zone (Col 2)
        b_zone = str(r[C["buying_zone"]]).strip() if len(r) > C["buying_zone"] else ""
        if b_zone in GITHUB_DATA_BUYING_ZONE_COLORS:
            bg_b, fg_b = GITHUB_DATA_BUYING_ZONE_COLORS[b_zone]
            reqs.append(color_cell_req(ws.id, rn, 2, bg_b, fg_b))
        # Total Score (Col 3)
        reqs.append({
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn + 1, "startColumnIndex": 3, "endColumnIndex": 4},
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.horizontalAlignment"
            }
        })
        # CMP (Col 4) - Currency
        reqs.append(color_cell_req(ws.id, rn, 4, "f1f8e9", "33691e", bold=False))
        reqs += get_currency_format_reqs(ws.id, rn, rn + 1, 4, 5)
        # Price Range (Col 5)
        if b_zone in GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS:
            lbg, lfg = GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS[b_zone]
            reqs.append(color_cell_req(ws.id, rn, 5, lbg, lfg, bold=False))
        # Portfolio Fit (Col 6)
        fit_bg, fit_fg = top10_fit_styles[idx]
        reqs.append(color_cell_req(ws.id, rn, 6, fit_bg, fit_fg, bold=True))
        # Tranche Guidance (Col 7)
        reqs.append(color_cell_req(ws.id, rn, 7, "f8f9fa", "495057", bold=False))
        # Action (Col 8)
        act = str(r[C["action"]]).strip() if len(r) > C["action"] else ""
        if act in GITHUB_DATA_ACTION_COLORS:
            bg_a, fg_a = GITHUB_DATA_ACTION_COLORS[act]
            reqs.append(color_cell_req(ws.id, rn, 8, bg_a, fg_a))

    # Main Watchlist Table Offset
    watchlist_start_idx = 2 + len(top10) + 1  # Group header banner row index
    col_hdr_idx = watchlist_start_idx + 1      # Column header row index
    data_start_idx = col_hdr_idx + 1          # First watchlist data row index

    # Group headers merge and colors
    for start_col, end_col, label in group_ranges:
        eff_start = FROZEN_COLS if (start_col < FROZEN_COLS <= end_col) else start_col
        if eff_start < end_col:
            reqs.append({
                "mergeCells": {
                    "range": {"sheetId": ws.id, "startRowIndex": watchlist_start_idx, "endRowIndex": watchlist_start_idx + 1,
                              "startColumnIndex": eff_start, "endColumnIndex": end_col + 1},
                    "mergeType": "MERGE_ALL"
                }
            })

    reqs += get_group_header_color_reqs(ws.id, group_ranges, frozen_cols=FROZEN_COLS, row_idx=watchlist_start_idx)

    # Column headers styling
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": col_hdr_idx, "endRowIndex": col_hdr_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": hex_rgb("37474f"),
                "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 9},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE"
            }},
            "fields": "userEnteredFormat"
        }
    })

    # Structural widths and freeze pane
    reqs += get_structural_format_reqs(ws.id, len(rows), num_cols, widths, freeze_rows=2, freeze_cols=1, header_row_idx=col_hdr_idx)

    # Number formats for percentage and currency columns
    pct_cols = [C["day_chg_pct"], C["return_1w"], C["return_1m"], C["div"], C["roe"], C["roa"], C["rev_growth"]]
    for col_idx in pct_cols:
        reqs += get_percentage_format_reqs(ws.id, data_start_idx, data_start_idx + len(rows), col_idx, col_idx + 1)

    curr_cols = [C["low52"], C["cmp"], C["high52"], C["eps"], C["bv"]]
    for col_idx in curr_cols:
        reqs += get_currency_format_reqs(ws.id, data_start_idx, data_start_idx + len(rows), col_idx, col_idx + 1)

    def sf(row, key):
        idx = C[key]
        try:
            v = str(row[idx]).replace("%", "").replace(",", "").replace("₹", "").replace(" Cr", "").strip()
            return float(v) if len(row) > idx and v else None
        except:
            return None

    # Row level colorings for full watchlist
    for i, row in enumerate(rows):
        rn = data_start_idx + i

        action    = str(row[C["action"]]).strip() if len(row) > C["action"] else ""
        b_zone    = str(row[C["buying_zone"]]).strip() if len(row) > C["buying_zone"] else ""
        tech_set  = str(row[C["technical_setup"]]).strip() if len(row) > C["technical_setup"] else ""
        trend_val = str(row[C["trend"]]).strip() if len(row) > C["trend"] else ""
        risk_val  = str(row[C["econ_sens"]]).strip() if len(row) > C["econ_sens"] else ""

        rsi_v    = sf(row, "rsi")
        pe_v     = sf(row, "pe")
        eps_v    = sf(row, "eps")
        pb_v     = sf(row, "pb")
        div_v    = sf(row, "div")
        roe_v    = sf(row, "roe")
        roa_v    = sf(row, "roa")
        debt_v   = sf(row, "debt_eq")
        growth_v = sf(row, "rev_growth")
        beta_v   = sf(row, "beta")
        vol_v    = sf(row, "vol_spike")
        q_sc     = sf(row, "quality")
        v_sc     = sf(row, "valuation")
        t_sc     = sf(row, "timing")
        tot_sc   = sf(row, "total")

        mcap_tier_v = sf(row, "mcap")
        if mcap_tier_v is not None:
            if mcap_tier_v >= 25000:     cb, cf = "d9ead3", "0b8043"
            elif mcap_tier_v >= 5000:    cb, cf = "d9eaf7", "1565c0"
            else:                        cb, cf = "fde9d9", "c62828"
            for key in ("symbol", "mcap"):
                reqs.append(color_cell_req(ws.id, rn, C[key], cb, cf))

        reqs.append(color_cell_req(ws.id, rn, C["high52"], "eaf4fb", "1565c0", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["low52"], "fdf2f2", "c62828", bold=False))

        day_chg_v = sf(row, "day_chg_pct")
        if day_chg_v is not None:
            if day_chg_v > 0:   reqs.append(color_cell_req(ws.id, rn, C["day_chg_pct"], "d9ead3", "0b8043"))
            elif day_chg_v < 0: reqs.append(color_cell_req(ws.id, rn, C["day_chg_pct"], "fde9d9", "c62828"))
            else:               reqs.append(color_cell_req(ws.id, rn, C["day_chg_pct"], "f1f1f1", "666666"))

        ret1w_v = sf(row, "return_1w")
        if ret1w_v is not None:
            if ret1w_v > 0:   reqs.append(color_cell_req(ws.id, rn, C["return_1w"], "d9ead3", "0b8043"))
            elif ret1w_v < 0: reqs.append(color_cell_req(ws.id, rn, C["return_1w"], "fde9d9", "c62828"))
            else:             reqs.append(color_cell_req(ws.id, rn, C["return_1w"], "f1f1f1", "666666"))

        ret1m_v = sf(row, "return_1m")
        if ret1m_v is not None:
            if ret1m_v > 0:   reqs.append(color_cell_req(ws.id, rn, C["return_1m"], "d9ead3", "0b8043"))
            elif ret1m_v < 0: reqs.append(color_cell_req(ws.id, rn, C["return_1m"], "fde9d9", "c62828"))
            else:             reqs.append(color_cell_req(ws.id, rn, C["return_1m"], "f1f1f1", "666666"))

        pct_high_v = sf(row, "pct_high")
        if pct_high_v is not None:
            if pct_high_v >= -20: reqs.append(color_cell_req(ws.id, rn, C["pct_high"], "d9ead3", "0b8043"))
            else:                 reqs.append(color_cell_req(ws.id, rn, C["pct_high"], "fde9d9", "c62828"))

        if pe_v is not None:
            if 0 < pe_v <= 25: reqs.append(color_cell_req(ws.id, rn, C["pe"], "d9ead3", "0b8043"))
            elif pe_v <= 40:   reqs.append(color_cell_req(ws.id, rn, C["pe"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["pe"], "fde9d9", "c62828"))

        if eps_v is not None:
            reqs.append(color_cell_req(ws.id, rn, C["eps"], "d9ead3", "0b8043") if eps_v > 0
                        else color_cell_req(ws.id, rn, C["eps"], "fde9d9", "c62828"))

        if pb_v is not None:
            if pb_v <= 3:      reqs.append(color_cell_req(ws.id, rn, C["pb"], "d9ead3", "0b8043"))
            elif pb_v <= 5:    reqs.append(color_cell_req(ws.id, rn, C["pb"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["pb"], "fde9d9", "c62828"))

        if div_v is not None:
            if div_v >= 2:     reqs.append(color_cell_req(ws.id, rn, C["div"], "d9ead3", "0b8043"))
            elif div_v >= 1:   reqs.append(color_cell_req(ws.id, rn, C["div"], "fff2cc", "7f4f00"))

        if rsi_v is not None:
            if rsi_v < 35:     reqs.append(color_cell_req(ws.id, rn, C["rsi"], "d9ead3", "0b8043"))
            elif rsi_v > 70:   reqs.append(color_cell_req(ws.id, rn, C["rsi"], "fde9d9", "c62828"))
            elif rsi_v > 60:   reqs.append(color_cell_req(ws.id, rn, C["rsi"], "fff2cc", "7f4f00"))

        if roe_v is not None:
            if roe_v >= 15:    reqs.append(color_cell_req(ws.id, rn, C["roe"], "d9ead3", "0b8043"))
            elif roe_v >= 8:   reqs.append(color_cell_req(ws.id, rn, C["roe"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["roe"], "fde9d9", "c62828"))

        if roa_v is not None:
            if roa_v >= 2:     reqs.append(color_cell_req(ws.id, rn, C["roa"], "d9ead3", "0b8043"))
            elif roa_v >= 1:   reqs.append(color_cell_req(ws.id, rn, C["roa"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["roa"], "fde9d9", "c62828"))

        if debt_v is not None:
            if debt_v <= 0.5:  reqs.append(color_cell_req(ws.id, rn, C["debt_eq"], "d9ead3", "0b8043"))
            elif debt_v <= 1:  reqs.append(color_cell_req(ws.id, rn, C["debt_eq"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["debt_eq"], "fde9d9", "c62828"))

        if growth_v is not None:
            if growth_v >= 10: reqs.append(color_cell_req(ws.id, rn, C["rev_growth"], "d9ead3", "0b8043"))
            elif growth_v >= 0: reqs.append(color_cell_req(ws.id, rn, C["rev_growth"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["rev_growth"], "fde9d9", "c62828"))

        if beta_v is not None:
            if beta_v <= 1:    reqs.append(color_cell_req(ws.id, rn, C["beta"], "d9ead3", "0b8043"))
            elif beta_v <= 1.5: reqs.append(color_cell_req(ws.id, rn, C["beta"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["beta"], "fde9d9", "c62828"))

        reqs.append(color_cell_req(ws.id, rn, C["strengths"], "f1f9f1", "0b8043", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["weaknesses"], "fdf2f2", "c62828", bold=False))

        if tech_set in TECHNICAL_SETUP_COLORS:
            bg, fg = TECHNICAL_SETUP_COLORS[tech_set]
            reqs.append(color_cell_req(ws.id, rn, C["technical_setup"], bg, fg))
        reqs.append(color_cell_req(ws.id, rn, C["cmp"], "f1f8e9", "33691e", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["fair_val"], "e8f5e9", "1b5e20", bold=False))
        if action in GITHUB_DATA_ACTION_COLORS:
            bg_a, fg_a = GITHUB_DATA_ACTION_COLORS[action]
            reqs.append(color_cell_req(ws.id, rn, C["action"], bg_a, fg_a))
        if b_zone in GITHUB_DATA_BUYING_ZONE_COLORS:
            bg_b, fg_b = GITHUB_DATA_BUYING_ZONE_COLORS[b_zone]
            reqs.append(color_cell_req(ws.id, rn, C["buying_zone"], bg_b, fg_b))
            if b_zone in GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS:
                lbg, lfg = GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS[b_zone]
                reqs.append(color_cell_req(ws.id, rn, C["price_range"], lbg, lfg, bold=False))

        if risk_val:
            if risk_val == "Very High": reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "fde9d9", "c62828"))
            elif risk_val in ("Medium-High", "High"): reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "ffe599", "7f4f00"))
            elif risk_val == "Medium": reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "fff2cc", "7f4f00"))
            elif risk_val in ("Low", "Low-Medium"): reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "d9ead3", "0b8043"))

        if q_sc is not None:
            if q_sc >= 30:   reqs.append(color_cell_req(ws.id, rn, C["quality"], "d9ead3", "0b8043"))
            elif q_sc <= 15: reqs.append(color_cell_req(ws.id, rn, C["quality"], "fde9d9", "c62828"))
        if v_sc is not None:
            if v_sc >= 22:   reqs.append(color_cell_req(ws.id, rn, C["valuation"], "d9ead3", "0b8043"))
            elif v_sc <= 10: reqs.append(color_cell_req(ws.id, rn, C["valuation"], "fde9d9", "c62828"))
        if t_sc is not None:
            if t_sc >= 22:   reqs.append(color_cell_req(ws.id, rn, C["timing"], "d9ead3", "0b8043"))
            elif t_sc <= 10: reqs.append(color_cell_req(ws.id, rn, C["timing"], "fde9d9", "c62828"))
        if tot_sc is not None:
            if   tot_sc >= 65: reqs.append(color_cell_req(ws.id, rn, C["total"], "00c853", "ffffff"))
            elif tot_sc >= 50: reqs.append(color_cell_req(ws.id, rn, C["total"], "d9ead3", "0b8043"))
            elif tot_sc >= 35: reqs.append(color_cell_req(ws.id, rn, C["total"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["total"], "fde9d9", "c62828"))

        if vol_v is not None:
            if vol_v >= 2:     reqs.append(color_cell_req(ws.id, rn, C["vol_spike"], "fde9d9", "c62828"))
            elif vol_v >= 1.5: reqs.append(color_cell_req(ws.id, rn, C["vol_spike"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, C["vol_spike"], "d9ead3", "0b8043"))

        if trend_val in TREND_COLORS:
            bg, fg = TREND_COLORS[trend_val]
            reqs.append(color_cell_req(ws.id, rn, C["trend"], bg, fg))

        news_sent = str(row[C["news_sentiment"]]).strip() if len(row) > C["news_sentiment"] else ""
        if "Bullish" in news_sent:
            reqs.append(color_cell_req(ws.id, rn, C["news_sentiment"], "d9ead3", "0b8043"))
        elif "Bearish" in news_sent:
            reqs.append(color_cell_req(ws.id, rn, C["news_sentiment"], "fde9d9", "c62828"))
        elif news_sent:
            reqs.append(color_cell_req(ws.id, rn, C["news_sentiment"], "f1f1f1", "555555"))

        reqs.append(color_cell_req(ws.id, rn, C["news_summary"], "e8f5f9", "01579b", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["news_source"],  "f5f5f5", "757575", bold=False))

    try:
        sheet_writer.batch_update_safe(sh, reqs)
    except Exception as e:
        log.error(f"[write_future_buy_tab] watchlist='{tab_name}' tab='{tab_name}' stage='apply formatting' "
                  f"failed ({type(e).__name__}): {e}. Row data was written but '{tab_name}' may be "
                  f"partially/un-styled until the next successful run.")
        raise

    log.info(f"{tab_name} tab written and formatted with Top 10 Opportunity Callout ({len(rows)} rows)")
    return ws


def process_all_watchlists(sh, nc_cache=None, shared_prices=None, shared_fund=None, shared_tech=None, shared_rev=None, sector_weights=None, portfolio_value=None):
    all_rows = {}
    for tab_name, symbols in WATCHLISTS.items():
        try:
            rows = process_watchlist_tab(
                sh, tab_name, symbols, nc_cache=nc_cache,
                shared_prices=shared_prices, shared_fund=shared_fund,
                shared_tech=shared_tech, shared_rev=shared_rev,
                sector_weights=sector_weights, portfolio_value=portfolio_value
            )
            all_rows[tab_name] = rows
        except Exception as e:
            log.error(
                f"[process_all_watchlists] watchlist='{tab_name}' tab='{tab_name}' FAILED "
                f"({type(e).__name__}): {e}. Worksheet was modified or cleared — '{tab_name}' may be in a partial/failed state.",
                exc_info=True,
            )
            raise
    return all_rows

