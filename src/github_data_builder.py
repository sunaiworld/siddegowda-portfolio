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
from score_engine import *
from sheet_formatter import *
from sheet_writer import *
import sheet_writer


# ══════════════════════════════════════════════
# ROW COLUMN MAP — mirrors the list built in build_result_row().
# Exported so telegram_bot / services read cached sheet rows
# without re-deriving indices.
# ══════════════════════════════════════════════
GITHUB_DATA_COLS = {
    # Identity & Size
    "symbol": 0, "sector": 1, "industry": 2, "archetype": 3,
    "econ_sens": 4, "inv_role": 5, "mcap": 6,
    # Price Position
    "low52": 7, "cmp": 8, "high52": 9, "pct_high": 10,
    # Immediate Momentum & Risk
    "day_chg_pct": 11, "return_1w": 12, "return_1m": 13, "return_3m": 14, "return_6m": 15, "trend": 16, "technical_setup": 17,
    "rsi": 18, "vol_spike": 19, "beta": 20,
    # Valuation
    "eps": 21, "pe": 22, "bv": 23, "pb": 24, "div": 25, "fair_val": 26,
    # Financial Health & Efficiency
    "rev_growth": 27, "roe": 28, "roa": 29, "debt_eq": 30,
    # Sentiment & Qualitative Data
    "news_summary": 31, "news_sentiment": 32, "news_source": 33,
    "strengths": 34, "weaknesses": 35,
    # Automated Scoring
    "quality": 36, "valuation": 37, "timing": 38, "total": 39,
    # Final Decision (MUST stay last)
    "buying_zone": 40, "price_range": 41, "action": 42,
}

# Header text per column key
GITHUB_DATA_HEADER_NAMES = {
    "symbol": "Symbol", "sector": "Sector", "industry": "Industry", "archetype": "Archetype",
    "econ_sens": "Economic Sensitivity", "inv_role": "Investor Role",
    "technical_setup": "Technical Setup",
    "low52": "52W Low", "cmp": "CMP", "high52": "52W High",
    "buying_zone": "Buying Zone", "fair_val": "Fair Val", "price_range": "Buy/Sell Price Range",
    "action": "Final Action", "trend": "Trend",
    "day_chg_pct": "Day Chg%", "return_1w": "1W Return %", "return_1m": "1M Return %", "return_3m": "3M Return %", "return_6m": "6M Return %", "pct_high": "Buy 20% Less",
    "pe": "PE", "eps": "EPS", "bv": "Book Value", "pb": "P/B",
    "div": "Div Yield%",
    "rsi": "RSI",
    "roe": "ROE%", "roa": "ROA%", "debt_eq": "Debt/Equity",
    "rev_growth": "Rev Growth%", "beta": "Beta",
    "strengths": "Strengths", "weaknesses": "Weaknesses",
    "quality": "Quality Score", "valuation": "Valuation Score", "timing": "Timing Score", "total": "Total Score",
    "vol_spike": "Vol Spike", "mcap": "Mkt Cap Cr",
    "news_summary":   "News Summary & Digest",
    "news_sentiment": "Sentiment & Score",
    "news_source":    "News Source",
}

# Column pixel width per key
GITHUB_DATA_COL_WIDTHS = {
    "symbol": 70, "sector": 75, "industry": 90, "archetype": 80,
    "econ_sens": 110, "inv_role": 110,
    "technical_setup": 110,
    "low52": 60, "cmp": 65, "high52": 60,
    "buying_zone": 115, "fair_val": 70, "price_range": 150,
    "action": 90, "trend": 90,
    "day_chg_pct": 55, "return_1w": 65, "return_1m": 65, "return_3m": 65, "return_6m": 65, "pct_high": 75,
    "pe": 45, "eps": 45, "bv": 55, "pb": 45,
    "div": 50,
    "rsi": 45,
    "roe": 50, "roa": 50, "debt_eq": 55,
    "rev_growth": 55, "beta": 45,
    "strengths": 200, "weaknesses": 200,
    "quality": 55, "valuation": 60, "timing": 55, "total": 55,
    "vol_spike": 55, "mcap": 70,
    "news_summary":   260,
    "news_sentiment": 110,
    "news_source":     80,
}

# Group label → (start_key, end_key inclusive) using GITHUB_DATA_COLS keys.
# Drives the merged banner row written above the normal column headers.
GROUP_DEFS = [
    ("symbol",       "mcap",        "Group 1: Identity & Size (Know what you're looking at)"),
    ("low52",        "pct_high",    "Group 2: Price Position (Where is it right now?)"),
    ("day_chg_pct",  "beta",        "Group 3: Immediate Momentum & Risk (Short-term action)"),
    ("eps",          "fair_val",    "Group 4: Valuation (Is the price justified?)"),
    ("rev_growth",   "debt_eq",     "Group 5: Financial Health & Efficiency (Business quality)"),
    ("news_summary", "weaknesses",  "Group 6: Sentiment & Qualitative Data (News and SWOT)"),
    ("quality",      "total",       "Group 7: Automated Scoring (System's composite ratings)"),
    ("buying_zone",  "action",      "Group 8: Final Decision (Actionable output - MUST be last)"),
]

# ══════════════════════════════════════════════
# BUILD A SINGLE RESULT ROW
# Shared by the Portfolio (GITHUB DATA) pipeline and any
# watchlist-only tab (e.g. Future Buy). Keeps row layout,
# scoring, and formatting identical everywhere.
# xirr_val is left as "" for watchlist symbols (no holdings).
# ══════════════════════════════════════════════
def build_result_row(sym, cmp, f, tech, rev_gr, xirr_val="", news_data=None):
    sector   = f.get("sector", "")
    industry = f.get("industry", "")
    high52   = f.get("high52")
    low52    = f.get("low52")
    mcap_cr  = f.get("mcap_cr")

    archetype = get_archetype(sym, sector, industry)
    econ_sens, inv_role = get_archetype_risk_profile(archetype)

    pct_high         = round((cmp - high52) / high52 * 100, 2) if high52 else ""
    pct_high_display = f"{pct_high}%" if pct_high != "" else ""
    mcap_fmt         = indian_cr(mcap_cr) if mcap_cr else ""

    rsi         = tech.get("rsi", "")
    sma200      = tech.get("sma200", "")
    vol_spike   = tech.get("vol_spike", "")
    trend       = tech.get("trend", "")
    cross       = tech.get("cross", "")
    day_chg_pct = tech.get("day_chg_pct", "")
    return_1w   = tech.get("return_1w", "")
    return_1m   = tech.get("return_1m", "")
    return_3m   = tech.get("return_3m", "")
    return_6m   = tech.get("return_6m", "")

    # News signals (already fetched by the news engine; zero cost here).
    # Passed as optional keys — scoring degrades gracefully to zero if absent.
    _nd = news_data or {}
    _bull = _nd.get("bullish_score", "")
    _bear = _nd.get("bearish_score", "")
    try:
        _bull = float(_bull) if _bull != "" else None
    except (TypeError, ValueError):
        _bull = None
    try:
        _bear = float(_bear) if _bear != "" else None
    except (TypeError, ValueError):
        _bear = None

    metrics = {
        "roe":                f.get("roe"),
        "roa":                f.get("roa"),
        "roce":               f.get("roce"),
        "rev_growth":         rev_gr,
        "debt_eq":            f.get("debt_eq"),
        "pe":                 f.get("pe"),
        "pb":                 f.get("pb"),
        "div":                f.get("div"),
        "rsi":                rsi if rsi != "" else None,
        "sma200":             sma200 if sma200 != "" else None,
        "cmp":                cmp,
        "vol_spike":          vol_spike if vol_spike != "" else None,
        "cross":              cross,
        # News signals — optional, never None-safe guard needed in scorer
        "news_sentiment":     _nd.get("sentiment", ""),
        "news_bullish_score": _bull,
        "news_bearish_score": _bear,
    }

    q_sc, v_sc, t_sc, tot_sc, final_action, strengths, weaknesses = compute_unified_score(
        sym, archetype, metrics
    )
    
    buying_zone = calculate_buying_zone(q_sc, v_sc, tot_sc, metrics)

    # Build price range metrics with EPS and BV for price range calculation
    price_range_metrics = dict(metrics)
    price_range_metrics["eps"] = f.get("eps")
    price_range_metrics["bv"]  = f.get("bv")
    price_range_metrics["cmp"] = cmp
    price_range, fair_val = calculate_price_range(archetype, price_range_metrics)

    strengths_str   = " | ".join(strengths)
    weaknesses_str  = " | ".join(weaknesses)
    technical_setup = classify_technical_setup(tech, cmp)

    C = GITHUB_DATA_COLS
    row = [""] * len(C)
    row[C["symbol"]]         = sym
    row[C["sector"]]         = sector
    row[C["industry"]]       = industry
    row[C["archetype"]]      = archetype
    row[C["econ_sens"]]      = econ_sens
    row[C["inv_role"]]       = inv_role
    row[C["technical_setup"]]= technical_setup
    row[C["low52"]]          = low52 or ""
    row[C["cmp"]]            = round(cmp, 2) if cmp else ""
    row[C["high52"]]         = high52 or ""
    row[C["buying_zone"]]    = buying_zone
    row[C["fair_val"]]       = fair_val
    row[C["price_range"]]    = price_range
    row[C["action"]]         = final_action
    row[C["trend"]]          = trend
    row[C["day_chg_pct"]]    = day_chg_pct
    row[C["return_1w"]]      = return_1w
    row[C["return_1m"]]      = return_1m
    row[C["return_3m"]]      = return_3m
    row[C["return_6m"]]      = return_6m
    row[C["pct_high"]]       = pct_high_display
    row[C["pe"]]             = f.get("pe") or ""
    row[C["eps"]]            = f.get("eps") or ""
    row[C["bv"]]             = f.get("bv") or ""
    row[C["pb"]]             = f.get("pb") or ""
    row[C["div"]]            = f.get("div") or ""
    row[C["rsi"]]            = rsi
    row[C["roe"]]            = f.get("roe") or ""
    row[C["roa"]]            = f.get("roa") or ""
    row[C["debt_eq"]]        = f.get("debt_eq") or ""
    row[C["rev_growth"]]     = rev_gr or ""
    row[C["beta"]]           = tech.get("beta_nifty") if tech.get("beta_nifty") is not None else (f.get("beta") or "")
    row[C["strengths"]]      = strengths_str
    row[C["weaknesses"]]     = weaknesses_str
    row[C["quality"]]        = q_sc
    row[C["valuation"]]      = v_sc
    row[C["timing"]]         = t_sc
    row[C["total"]]          = tot_sc
    row[C["vol_spike"]]      = vol_spike
    row[C["mcap"]]           = mcap_fmt

    # ── News Engine fields (Phase 3 Condensed: 3 columns) ─────────
    nd = news_data or {}
    digest = nd.get("digest", "") or ""
    reason = nd.get("reason", "") or ""
    if digest and reason and reason not in digest:
        summary_full = f"{digest} ({reason})"
    else:
        summary_full = digest or reason or ""

    sentiment = nd.get("sentiment", "") or ""
    bull_sc = nd.get("bullish_score")
    bear_sc = nd.get("bearish_score")
    if sentiment == "Bullish" and bull_sc is not None:
        sent_str = f"{sentiment} ({int(bull_sc)}/10)"
    elif sentiment == "Bearish" and bear_sc is not None:
        sent_str = f"{sentiment} ({int(bear_sc)}/10)"
    elif sentiment:
        sent_str = sentiment
    else:
        sent_str = ""

    row[C["news_summary"]]   = summary_full
    row[C["news_sentiment"]] = sent_str
    row[C["news_source"]]    = nd.get("source", "")

    return row, archetype, tot_sc, final_action


def clean_row(row):
    return [("" if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for v in row]


def write_github_data(sh, rows, tab_name="GITHUB DATA"):
    C = GITHUB_DATA_COLS
    num_cols = len(C)

    headers = [""] * num_cols
    widths  = [70] * num_cols
    for key, idx in C.items():
        headers[idx] = GITHUB_DATA_HEADER_NAMES.get(key, key)
        widths[idx]  = GITHUB_DATA_COL_WIDTHS.get(key, 70)

    # Group header row — one label per group, placed in the leftmost cell
    # of its range. mergeCells (below) visually spans it across the group.
    # FROZEN_COLS must match freeze_cols passed to get_structural_format_reqs
    # below: Sheets API refuses to merge a range spanning the frozen/
    # non-frozen boundary, so any group crossing it (currently just Group 1,
    # since Symbol/col A is frozen) gets its label placed at the first
    # unfrozen column instead — matching where the merge actually starts.
    FROZEN_COLS = 1
    group_ranges = [(C[sk], C[ek], label) for sk, ek, label in GROUP_DEFS]
    group_row = [""] * num_cols
    for start_col, end_col, label in group_ranges:
        label_col = FROZEN_COLS if (start_col < FROZEN_COLS <= end_col) else start_col
        group_row[label_col] = label

    all_data = [group_row, headers]
    if rows:
        all_data.extend([clean_row(r) for r in rows])

    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(tab_name, rows=len(all_data) + 20, cols=num_cols)

    try:
        sheet_writer.clear_sheet_safe(ws)
    except Exception as e:
        log.error(f"[write_github_data] watchlist='{tab_name}' tab='{tab_name}' stage='clear worksheet' failed ({type(e).__name__}): {e}")
        raise

    try:
        sheet_writer.batch_update_safe(sh, clear_all_formatting_reqs(ws.id))
    except Exception as e:
        log.error(f"[write_github_data] watchlist='{tab_name}' tab='{tab_name}' stage='clear formatting' failed ({type(e).__name__}): {e}")
        raise

    try:
        sheet_writer.update_sheet_safe(ws, "A1", all_data, value_input_option="USER_ENTERED")
    except Exception as e:
        log.error(f"[write_github_data] watchlist='{tab_name}' tab='{tab_name}' stage='write row data + headers' failed ({type(e).__name__}): {e}. "
                  f"Worksheet was already cleared — '{tab_name}' has no data/formatting until the next successful run.")
        raise

    clear_filter_req = {"clearBasicFilter": {"sheetId": ws.id}}
    reqs = [clear_filter_req] + build_github_data_format_requests(
        ws.id, rows, start_row=0, freeze_rows=2, freeze_cols=1
    )

    try:
        sheet_writer.batch_update_safe(sh, reqs)
    except Exception as e:
        log.error(f"[write_github_data] watchlist='{tab_name}' tab='{tab_name}' stage='apply formatting' "
                  f"failed ({type(e).__name__}): {e}. Row data was written but '{tab_name}' may be "
                  f"partially/un-styled until the next successful run.")
        raise

    log.info(f"{tab_name} tab written and formatted ({len(rows)} rows)")
    return ws
