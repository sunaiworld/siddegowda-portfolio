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
import history_tracker
import portfolio_analytics
import news_engine.news_cache as news_cache
from news_engine.sources import google_news_rss
from news_engine import classifier

log = logging.getLogger("portfolio")

from config import *
from score_engine import *
from sheet_formatter import *
from sheet_writer import *


# ══════════════════════════════════════════════
# ROW COLUMN MAP — mirrors the list built in build_result_row().
# Exported so telegram_bot / services read cached sheet rows
# without re-deriving indices.
# ══════════════════════════════════════════════
GITHUB_DATA_COLS = {
    "symbol": 0, "sector": 1, "industry": 2, "archetype": 3,
    "econ_sens": 4, "inv_role": 5,
    "low52": 6, "high52": 7, "day_chg_pct": 8, "pct_high": 9,
    "pe": 10, "eps": 11, "bv": 12, "pb": 13,
    "div": 14,
    "rsi": 15,
    "roe": 16, "roa": 17, "debt_eq": 18,
    "rev_growth": 19, "beta": 20,
    "strengths": 21, "weaknesses": 22, "technical_setup": 23,
    "action": 24, "trend": 25,
    "quality": 26, "valuation": 27, "timing": 28, "total": 29,
    "vol_spike": 30,
    "mcap": 31, "cap_type": 32,
    # ── News Engine columns (Phase A) ──────────────────────
    "news_summary":   33,
    "bullish_score":  34,
    "bearish_score":  35,
    "news_sentiment": 36,
    "news_reason":    37,
    "news_source":    38,
}

# Header text per column key
GITHUB_DATA_HEADER_NAMES = {
    "symbol": "Symbol", "sector": "Sector", "industry": "Industry", "archetype": "Archetype",
    "econ_sens": "Economic Sensitivity", "inv_role": "Investor Role",
    "low52": "52W Low", "high52": "52W High", "day_chg_pct": "Day Chg%", "pct_high": "Buy 20% Less",
    "pe": "PE", "eps": "EPS", "bv": "Book Value", "pb": "P/B",
    "div": "Div Yield%",
    "rsi": "RSI",
    "roe": "ROE%", "roa": "ROA%", "debt_eq": "Debt/Equity",
    "rev_growth": "Rev Growth%", "beta": "Beta",
    "strengths": "Strengths", "weaknesses": "Weaknesses",
    "technical_setup": "Technical Setup",
    "action": "Final Action", "trend": "Trend",
    "quality": "Quality Score", "valuation": "Valuation Score", "timing": "Timing Score", "total": "Total Score",
    "vol_spike": "Vol Spike",
    "mcap": "Mkt Cap Cr", "cap_type": "Cap Type",
    # ── News Engine columns (Phase A) ──────────────────────
    "news_summary":   "News Summary",
    "bullish_score":  "Bullish Score",
    "bearish_score":  "Bearish Score",
    "news_sentiment": "News Sentiment",
    "news_reason":    "News Reason",
    "news_source":    "News Source",
}

# Column pixel width per key
GITHUB_DATA_COL_WIDTHS = {
    "symbol": 70, "sector": 75, "industry": 90, "archetype": 80,
    "econ_sens": 130, "inv_role": 130,
    "low52": 55, "high52": 55, "day_chg_pct": 60, "pct_high": 65,
    "pe": 45, "eps": 45, "bv": 55, "pb": 45,
    "div": 50,
    "rsi": 45,
    "roe": 50, "roa": 50, "debt_eq": 55,
    "rev_growth": 55, "beta": 45,
    "strengths": 200, "weaknesses": 200,
    "technical_setup": 110,
    "action": 90, "trend": 90,
    "quality": 55, "valuation": 60, "timing": 55, "total": 55,
    "vol_spike": 55,
    "mcap": 65, "cap_type": 65,
    # ── News Engine columns (Phase A) ──────────────────────
    "news_summary":   220,
    "bullish_score":   55,
    "bearish_score":   55,
    "news_sentiment":  80,
    "news_reason":    180,
    "news_source":     80,
}


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

    cap_type = ""
    if mcap_cr:
        if   mcap_cr >= 25000: cap_type = "Large Cap"
        elif mcap_cr >= 5000:  cap_type = "Mid Cap"
        else:                   cap_type = "Small Cap"

    pct_high         = round((cmp - high52) / high52 * 100, 2) if high52 else ""
    pct_high_display = f"{pct_high}%" if pct_high != "" else ""
    mcap_fmt         = indian_cr(mcap_cr) if mcap_cr else ""

    rsi         = tech.get("rsi", "")
    sma200      = tech.get("sma200", "")
    vol_spike   = tech.get("vol_spike", "")
    trend       = tech.get("trend", "")
    cross       = tech.get("cross", "")
    day_chg_pct = tech.get("day_chg_pct", "")

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

    strengths_str   = " | ".join(strengths)
    weaknesses_str  = " | ".join(weaknesses)
    technical_setup = classify_technical_setup(tech, cmp)

    # Row is built BY KEY via GITHUB_DATA_COLS, not by position in a
    # literal list — reordering the columns is now a one-line change
    # to GITHUB_DATA_COLS, nothing here needs to change to match.
    C = GITHUB_DATA_COLS
    row = [""] * len(C)
    row[C["symbol"]]          = sym
    row[C["sector"]]          = sector
    row[C["industry"]]        = industry
    row[C["archetype"]]       = archetype
    row[C["econ_sens"]]       = econ_sens
    row[C["inv_role"]]        = inv_role
    row[C["low52"]]           = low52 or ""
    row[C["high52"]]          = high52 or ""
    row[C["day_chg_pct"]]     = day_chg_pct
    row[C["pct_high"]]        = pct_high_display
    row[C["pe"]]              = f.get("pe") or ""
    row[C["eps"]]             = f.get("eps") or ""
    row[C["bv"]]              = f.get("bv") or ""
    row[C["pb"]]              = f.get("pb") or ""
    row[C["div"]]             = f.get("div") or ""
    row[C["rsi"]]             = rsi
    row[C["roe"]]             = f.get("roe") or ""
    row[C["roa"]]             = f.get("roa") or ""
    row[C["debt_eq"]]         = f.get("debt_eq") or ""
    row[C["rev_growth"]]      = rev_gr or ""
    row[C["beta"]]            = f.get("beta") or ""
    row[C["strengths"]]       = strengths_str
    row[C["weaknesses"]]      = weaknesses_str
    row[C["technical_setup"]] = technical_setup
    row[C["action"]]          = final_action
    row[C["quality"]]         = q_sc
    row[C["valuation"]]       = v_sc
    row[C["timing"]]          = t_sc
    row[C["total"]]           = tot_sc
    row[C["vol_spike"]]       = vol_spike
    row[C["trend"]]           = trend
    row[C["mcap"]]            = mcap_fmt
    row[C["cap_type"]]        = cap_type

    # ── News Engine fields (Phase A) ─────────────────────────────
    nd = news_data or {}
    row[C["news_summary"]]   = nd.get("digest", "")
    row[C["bullish_score"]]  = nd.get("bullish_score", "")
    row[C["bearish_score"]]  = nd.get("bearish_score", "")
    row[C["news_sentiment"]] = nd.get("sentiment", "")
    row[C["news_reason"]]    = nd.get("reason", "")
    row[C["news_source"]]    = nd.get("source", "")

    return row, archetype, tot_sc, final_action

def clean_row(row):
    return [("" if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for v in row]

def write_github_data(sh, rows, tab_name="GITHUB DATA"):
    C = GITHUB_DATA_COLS
    num_cols = len(C)

    import gspread.exceptions as _gse

    try:
        ws = sh.worksheet(tab_name)
    except _gse.WorksheetNotFound:
        ws = sh.add_worksheet(tab_name, rows=300, cols=num_cols)

    # Always clear after resolving the worksheet — outside the try so
    # any transient error here propagates instead of falling through to
    # add_worksheet() on an already-existing sheet (the root cause of
    # the "sheet already exists" 400 error).
    for _attempt in range(3):
        try:
            ws.clear()
            break
        except _gse.APIError as _e:
            if "429" in str(_e) and _attempt < 2:
                time.sleep(20)
            else:
                raise


    # Headers and widths assembled BY KEY from GITHUB_DATA_COLS — the
    # only place column order is ever defined. Nothing else can drift.
    headers = [""] * num_cols
    widths  = [70] * num_cols
    for key, idx in C.items():
        headers[idx] = GITHUB_DATA_HEADER_NAMES.get(key, key)
        widths[idx]  = GITHUB_DATA_COL_WIDTHS.get(key, 70)

    # Harden header write against transient 429 quota errors.
    for _attempt in range(5):
        try:
            ws.append_row(headers)
            break
        except _gse.APIError as _e:
            if "429" in str(_e):
                _wait = 15 * (2 ** _attempt)   # 15 s, 30 s, 60 s, 120 s, 240 s
                log.warning(f"[write_github_data] 429 on append_row(headers), waiting {_wait}s (attempt {_attempt+1}/5)")
                time.sleep(_wait)
            else:
                raise
    if rows:
        rows = [clean_row(r) for r in rows]
        ws.append_rows(rows)

    # ── Structural formatting: header, freeze, row height, column widths ──
    # Consolidated into a single batch_update.
    struct_reqs = get_structural_format_reqs(ws.id, len(rows), num_cols, widths, freeze_rows=1, freeze_cols=1)
    
    reqs = struct_reqs


    ACTION_COLORS = {
        "STRONG BUY":  ("00c853", "ffffff"),
        "BUY":         ("0b8043", "ffffff"),
        "ACCUMULATE":  ("d9ead3", "0b8043"),
        "HOLD":        ("fff2cc", "7f4f00"),
        "WATCH":       ("fce8b2", "7f4f00"),
        "AVOID":       ("fde9d9", "c62828"),
        "SELL":        ("cc0000", "ffffff"),
    }

    def sf(row, key):
        idx = C[key]
        try:
            v = str(row[idx]).replace("%", "").replace(",", "").replace("₹", "").replace(" Cr", "").strip()
            return float(v) if len(row) > idx and v else None
        except:
            return None

    for i, row in enumerate(rows):
        rn  = i + 1

        cap       = str(row[C["cap_type"]]).strip() if len(row) > C["cap_type"] else ""
        action    = str(row[C["action"]]).strip() if len(row) > C["action"] else ""
        tech_set  = str(row[C["technical_setup"]]).strip() if len(row) > C["technical_setup"] else ""
        trend_val = str(row[C["trend"]]).strip() if len(row) > C["trend"] else ""
        risk_val  = str(row[C["econ_sens"]]).strip() if len(row) > C["econ_sens"] else ""

        pct      = sf(row, "pct_high")
        day_chg  = sf(row, "day_chg_pct")
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

        # ── Cap Type family: Symbol, Mkt Cap Cr, Cap Type ──
        if cap == "Large Cap":       cb, cf = "d9ead3", "0b8043"
        elif cap == "Mid Cap":       cb, cf = "d9eaf7", "1565c0"
        elif cap == "Small Cap":     cb, cf = "fde9d9", "c62828"
        else:                        cb, cf = None, None
        if cb:
            for key in ("symbol", "mcap", "cap_type"):
                reqs.append(color_cell_req(ws.id, rn, C[key], cb, cf))

        # ── 52W High / Low ──
        reqs.append(color_cell_req(ws.id, rn, C["high52"], "eaf4fb", "1565c0", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["low52"], "fdf2f2", "c62828", bold=False))

        # ── Day Chg% ──
        if day_chg is not None:
            reqs.append(color_cell_req(ws.id, rn, C["day_chg_pct"], "d9ead3", "0b8043") if day_chg > 0
                        else color_cell_req(ws.id, rn, C["day_chg_pct"], "fde9d9", "c62828") if day_chg < 0
                        else color_cell_req(ws.id, rn, C["day_chg_pct"], "f1f1f1", "666666"))

        # ── Buy 20% Less (pct from 52W high) ──
        if pct is not None:
            reqs.append(color_cell_req(ws.id, rn, C["pct_high"], "d9ead3", "0b8043") if pct >= -20
                        else color_cell_req(ws.id, rn, C["pct_high"], "fde9d9", "c62828"))

        # ── PE ──
        if pe_v is not None:
            if 0 < pe_v <= 25:   reqs.append(color_cell_req(ws.id, rn, C["pe"], "d9ead3", "0b8043"))
            elif pe_v <= 40:     reqs.append(color_cell_req(ws.id, rn, C["pe"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["pe"], "fde9d9", "c62828"))

        # ── EPS ──
        if eps_v is not None:
            reqs.append(color_cell_req(ws.id, rn, C["eps"], "d9ead3", "0b8043") if eps_v > 0
                        else color_cell_req(ws.id, rn, C["eps"], "fde9d9", "c62828"))

        # ── P/B ──
        if pb_v is not None:
            if pb_v <= 3:        reqs.append(color_cell_req(ws.id, rn, C["pb"], "d9ead3", "0b8043"))
            elif pb_v <= 5:      reqs.append(color_cell_req(ws.id, rn, C["pb"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["pb"], "fde9d9", "c62828"))

        # ── Div Yield% ──
        if div_v is not None:
            if div_v >= 2:       reqs.append(color_cell_req(ws.id, rn, C["div"], "d9ead3", "0b8043"))
            elif div_v >= 1:     reqs.append(color_cell_req(ws.id, rn, C["div"], "fff2cc", "7f4f00"))

        # ── RSI ──
        if rsi_v is not None:
            if   rsi_v < 35:  reqs.append(color_cell_req(ws.id, rn, C["rsi"], "d9ead3", "0b8043"))
            elif rsi_v > 70:  reqs.append(color_cell_req(ws.id, rn, C["rsi"], "fde9d9", "c62828"))
            elif rsi_v > 60:  reqs.append(color_cell_req(ws.id, rn, C["rsi"], "fff2cc", "7f4f00"))

        # ── ROE% / ROA% / Debt-Equity / Rev Growth% / Beta ──
        if roe_v is not None:
            if roe_v >= 15:      reqs.append(color_cell_req(ws.id, rn, C["roe"], "d9ead3", "0b8043"))
            elif roe_v >= 8:     reqs.append(color_cell_req(ws.id, rn, C["roe"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["roe"], "fde9d9", "c62828"))
        if roa_v is not None:
            if roa_v >= 2:       reqs.append(color_cell_req(ws.id, rn, C["roa"], "d9ead3", "0b8043"))
            elif roa_v >= 1:     reqs.append(color_cell_req(ws.id, rn, C["roa"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["roa"], "fde9d9", "c62828"))
        if debt_v is not None:
            if debt_v <= 0.5:    reqs.append(color_cell_req(ws.id, rn, C["debt_eq"], "d9ead3", "0b8043"))
            elif debt_v <= 1:    reqs.append(color_cell_req(ws.id, rn, C["debt_eq"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["debt_eq"], "fde9d9", "c62828"))
        if growth_v is not None:
            if growth_v >= 10:   reqs.append(color_cell_req(ws.id, rn, C["rev_growth"], "d9ead3", "0b8043"))
            elif growth_v >= 0:  reqs.append(color_cell_req(ws.id, rn, C["rev_growth"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["rev_growth"], "fde9d9", "c62828"))
        if beta_v is not None:
            if beta_v <= 1:      reqs.append(color_cell_req(ws.id, rn, C["beta"], "d9ead3", "0b8043"))
            elif beta_v <= 1.5:  reqs.append(color_cell_req(ws.id, rn, C["beta"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws.id, rn, C["beta"], "fde9d9", "c62828"))

        # ── Strengths / Weaknesses ──
        reqs.append(color_cell_req(ws.id, rn, C["strengths"], "f1f9f1", "0b8043", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["weaknesses"], "fdf2f2", "c62828", bold=False))

        # ── Technical Setup / Final Action / Risk Level ──
        if tech_set in TECHNICAL_SETUP_COLORS:
            bg, fg = TECHNICAL_SETUP_COLORS[tech_set]
            reqs.append(color_cell_req(ws.id, rn, C["technical_setup"], bg, fg))
        if action in ACTION_COLORS:
            bg_a, fg_a = ACTION_COLORS[action]
            reqs.append(color_cell_req(ws.id, rn, C["action"], bg_a, fg_a))
        
        if risk_val:
            if risk_val == "Very High": reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "fde9d9", "c62828"))
            elif risk_val in ("Medium-High", "High"): reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "ffe599", "7f4f00"))
            elif risk_val == "Medium": reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "fff2cc", "7f4f00"))
            elif risk_val in ("Low", "Low-Medium"): reqs.append(color_cell_req(ws.id, rn, C["econ_sens"], "d9ead3", "0b8043"))

        # ── Quality / Valuation / Timing / Total ──
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

        # ── Vol Spike / Trend ──
        if vol_v is not None:
            if vol_v >= 2:      reqs.append(color_cell_req(ws.id, rn, C["vol_spike"], "fde9d9", "c62828"))
            elif vol_v >= 1.5:  reqs.append(color_cell_req(ws.id, rn, C["vol_spike"], "fff2cc", "7f4f00"))
            else:               reqs.append(color_cell_req(ws.id, rn, C["vol_spike"], "d9ead3", "0b8043"))
        if trend_val in TREND_COLORS:
            bg, fg = TREND_COLORS[trend_val]
            reqs.append(color_cell_req(ws.id, rn, C["trend"], bg, fg))

        # ── AI News columns ────────────────────────────────────────────────
        bull_v = sf(row, "bullish_score")
        bear_v = sf(row, "bearish_score")
        news_sent = str(row[C["news_sentiment"]]).strip() if len(row) > C["news_sentiment"] else ""

        if bull_v is not None:
            if bull_v >= 6:   reqs.append(color_cell_req(ws.id, rn, C["bullish_score"], "d9ead3", "0b8043"))
            elif bull_v >= 3: reqs.append(color_cell_req(ws.id, rn, C["bullish_score"], "fff2cc", "7f4f00"))
            else:             reqs.append(color_cell_req(ws.id, rn, C["bullish_score"], "fde9d9", "c62828"))

        if bear_v is not None:
            if bear_v >= 6:   reqs.append(color_cell_req(ws.id, rn, C["bearish_score"], "fde9d9", "c62828"))
            elif bear_v >= 3: reqs.append(color_cell_req(ws.id, rn, C["bearish_score"], "fff2cc", "7f4f00"))
            else:             reqs.append(color_cell_req(ws.id, rn, C["bearish_score"], "d9ead3", "0b8043"))

        if news_sent == "Bullish":
            reqs.append(color_cell_req(ws.id, rn, C["news_sentiment"], "d9ead3", "0b8043"))
        elif news_sent == "Bearish":
            reqs.append(color_cell_req(ws.id, rn, C["news_sentiment"], "fde9d9", "c62828"))
        elif news_sent:
            reqs.append(color_cell_req(ws.id, rn, C["news_sentiment"], "f1f1f1", "555555"))

        reqs.append(color_cell_req(ws.id, rn, C["news_summary"], "e8f5f9", "01579b", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["news_reason"],  "e8f5f9", "01579b", bold=False))
        reqs.append(color_cell_req(ws.id, rn, C["news_source"],    "f5f5f5", "757575", bold=False))

    batch_update_safe(sh, reqs)
    log.info(f"{tab_name} tab written and formatted ({len(rows)} rows)")
    return ws

