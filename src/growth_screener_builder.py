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
from sheet_writer import *
from github_data_builder import *



# ══════════════════════════════════════════════
# WRITE GROWTH SCREENER TAB
# ══════════════════════════════════════════════
def write_growth_screener(sh, all_out):
    ACTION_COLORS = {
        "STRONG BUY":  ("ffffff", "00c853"),
        "BUY":         ("ffffff", "0b8043"),
        "ACCUMULATE":  ("0b8043", "d9ead3"),
        "HOLD":        ("7f4f00", "fff2cc"),
        "WATCH":       ("7f4f00", "fce8b2"),
        "AVOID":       ("c62828", "fde9d9"),
        "SELL":        ("ffffff", "cc0000"),
    }
    # Use GITHUB_DATA_COLS so column order changes in main never break
    # the Growth Screener. This is the same defensive pattern used in
    # write_github_data() and history_tracker.py.
    C = GITHUB_DATA_COLS

    def _cell(row, key):
        idx = C.get(key)
        if idx is None or len(row) <= idx:
            return ""
        return str(row[idx])

    def sf(v):
        try: return float(str(v).replace("%", "").replace(",", "").replace("₹", "").replace(" Cr", "").strip())
        except: return None

    def sf_k(row, key):
        return sf(_cell(row, key))

    growth = []
    for row in all_out:
        if not row or not row[0]: continue
        sym    = row[0].strip()
        action = _cell(row, "action")
        tot_sc = sf_k(row, "total")
        q_sc   = sf_k(row, "quality")
        v_sc   = sf_k(row, "valuation")
        t_sc   = sf_k(row, "timing")
        rsi    = _cell(row, "rsi")
        trend  = _cell(row, "trend")

        growth.append([
            sym,
            _cell(row, "pe"),           # col  2: PE
            _cell(row, "roe"),          # col  3: ROE%
            _cell(row, "debt_eq"),      # col  4: Debt/Eq
            _cell(row, "rev_growth"),   # col  5: Rev Growth%
            _cell(row, "div"),          # col  6: Div Yield%
            _cell(row, "pct_high"),     # col  7: Buy 20% Less
            q_sc or "", v_sc or "", t_sc or "", tot_sc or "",  # 8 9 10 11
            action,                     # col 12: Final Action
            _cell(row, "strengths"),    # col 13: Strengths
            _cell(row, "weaknesses"),   # col 14: Weaknesses
            rsi, trend,                 # col 15: RSI  col 16: Trend
        ])

    growth.sort(key=lambda x: float(x[10]) if x[10] != "" else 0, reverse=True)


    try:
        gsw = sh.worksheet("Growth Screener")
        gsw.clear()
    except:
        gsw = sh.add_worksheet("Growth Screener", rows=200, cols=17)

    gsw.append_row([
        "Symbol",
        "PE", "ROE%", "Debt/Eq", "Rev Growth%", "Div Yield%", "Buy 20% Less",
        "Quality", "Valuation", "Timing", "Total Score",
        "Final Action",
        "Strengths", "Weaknesses",
        "RSI", "Trend"
    ])
    if growth: gsw.append_rows(growth)

    reqs = [{"repeatCell": {
        "range": {"sheetId": gsw.id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": 17},
        "cell": {"userEnteredFormat": {
            "backgroundColor": hex_rgb("0d1b2a"),
            "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 8},
            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"
        }},
        "fields": "userEnteredFormat"
    }}]
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": gsw.id, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }})
    gs_widths = [80, 80, 50, 55, 60, 70, 65, 80, 55, 60, 55, 60, 90, 220, 220, 50, 90]
    reqs += [{"updateDimensionProperties": {
        "range": {"sheetId": gsw.id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize"
    }} for i, w in enumerate(gs_widths)]

    for i, row in enumerate(growth):
        rn  = i + 1
        alt = "f8f9fa" if i % 2 == 0 else "ffffff"
        action = str(row[12])
        reqs.append({"repeatCell": {
            "range": {"sheetId": gsw.id, "startRowIndex": rn, "endRowIndex": rn + 1,
                      "startColumnIndex": 0, "endColumnIndex": 17},
            "cell": {"userEnteredFormat": {"backgroundColor": hex_rgb(alt)}},
            "fields": "userEnteredFormat.backgroundColor"
        }})

        if action in ACTION_COLORS:
            fg_a, bg_a = ACTION_COLORS[action]
            reqs.append(color_cell_req(gsw.id, rn, 12, bg_a, fg_a))

        cap = str(row[1])
        if   cap == "Large Cap": reqs.append(color_cell_req(gsw.id, rn, 1, "d9ead3", "0b8043"))
        elif cap == "Mid Cap":   reqs.append(color_cell_req(gsw.id, rn, 1, "d9eaf7", "1565c0"))
        elif cap == "Small Cap": reqs.append(color_cell_req(gsw.id, rn, 1, "fde9d9", "c62828"))

        try:
            rsi_val = float(str(row[15]).replace("%", ""))
            if   rsi_val < 35: reqs.append(color_cell_req(gsw.id, rn, 15, "d9ead3", "0b8043"))
            elif rsi_val > 70: reqs.append(color_cell_req(gsw.id, rn, 15, "fde9d9", "c62828"))
        except: pass

        try:
            tot = float(str(row[11]))
            if   tot >= 65: reqs.append(color_cell_req(gsw.id, rn, 11, "00c853", "ffffff"))
            elif tot >= 50: reqs.append(color_cell_req(gsw.id, rn, 11, "d9ead3", "0b8043"))
            elif tot >= 35: reqs.append(color_cell_req(gsw.id, rn, 11, "fff2cc", "7f4f00"))
            else:           reqs.append(color_cell_req(gsw.id, rn, 11, "fde9d9", "c62828"))
        except: pass

    for col_idx in [13, 14]:
        reqs.append({"repeatCell": {
            "range": {"sheetId": gsw.id, "startRowIndex": 1, "endRowIndex": len(growth) + 1,
                      "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat.wrapStrategy"
        }})

    batch_update_safe(sh, reqs)
    log.info(f"Growth Screener: {len(growth)} stocks")
    return growth

