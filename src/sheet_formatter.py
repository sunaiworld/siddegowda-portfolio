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



# ══════════════════════════════════════════════
# WRITE A GITHUB-DATA-STYLE TAB
# Generic writer reused for "GITHUB DATA" and any
# watchlist tab (e.g. "Future Buy") — identical layout,
# sorting-ready columns, search/filter-friendly headers,
# styling, and coloring rules.
# ══════════════════════════════════════════════

TECHNICAL_SETUP_COLORS = {
    "🟣 Breakout":      ("e1d5f7", "6a1b9a"),
    "🟢 Tight Base":    ("d9ead3", "0b8043"),
    "🔵 Pullback":      ("d9eaf7", "1565c0"),
    "🔴 Extended":      ("fde9d9", "c62828"),
    "🟡 Consolidating": ("fff2cc", "7f4f00"),
    "🟠 Volatile":      ("fce8b2", "7f4f00"),
    "⚪ Unknown":       ("f1f1f1", "666666"),
}


TREND_COLORS = {
    "Strong Uptrend":   ("00c853", "ffffff"),
    "Uptrend":          ("d9ead3", "0b8043"),
    "Sideways":         ("fff2cc", "7f4f00"),
    "Downtrend":        ("fde9d9", "c62828"),
    "Strong Downtrend": ("cc0000", "ffffff"),
}


# ══════════════════════════════════════════════
# FORMAT HELPERS
# ══════════════════════════════════════════════
def indian_cr(value):
    try:
        val = int(round(float(value)))
        if val == 0: return ""
        s = str(val)
        if len(s) <= 3: return f"₹{s} Cr"
        last3 = s[-3:]; rest = s[:-3]; parts = []
        while len(rest) > 2:
            parts.append(rest[-2:]); rest = rest[:-2]
        if rest: parts.append(rest)
        parts.reverse()
        return "₹" + ",".join(parts) + "," + last3 + " Cr"
    except:
        return ""


def hex_rgb(h):
    h = h.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def color_cell_req(sheet_id, row_idx, col_idx, bg, fg, bold=True):
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1
            },
            "cell": {"userEnteredFormat": {
                "backgroundColor": hex_rgb(bg),
                "textFormat": {"foregroundColor": hex_rgb(fg), "bold": bold}
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)"
        }
    }


def get_structural_format_reqs(ws_id, num_rows, num_cols, widths=None, freeze_rows=1, freeze_cols=1):
    reqs = []
    # Header format
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {
                "backgroundColor": hex_rgb("0d1b2a"),
                "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 8},
                "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"
            }},
            "fields": "userEnteredFormat"
        }
    })
    # Freeze row/col
    reqs.append({
        "updateSheetProperties": {
            "properties": {"sheetId": ws_id, "gridProperties": {"frozenRowCount": freeze_rows, "frozenColumnCount": freeze_cols}},
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
        }
    })
    # Row height
    reqs.append({
        "updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 50}, "fields": "pixelSize"
        }
    })
    # Column widths
    if widths:
        reqs += [{"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"
        }} for i, w in enumerate(widths)]

    # Alternating row colors
    for i in range(num_rows):
        rn = i + 1
        alt = "f8f9fa" if i % 2 == 0 else "ffffff"
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": rn, "endRowIndex": rn + 1,
                      "startColumnIndex": 0, "endColumnIndex": num_cols},
            "cell": {"userEnteredFormat": {"backgroundColor": hex_rgb(alt)}},
            "fields": "userEnteredFormat.backgroundColor"
        }})
    return reqs

def get_currency_format_reqs(ws_id, start_row, end_row, start_col, end_col):
    return [{
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": start_row, "endRowIndex": end_row,
                      "startColumnIndex": start_col, "endColumnIndex": end_col},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"₹"#,##0.00'}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    }]

def get_percentage_format_reqs(ws_id, start_row, end_row, start_col, end_col):
    return [{
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": start_row, "endRowIndex": end_row,
                      "startColumnIndex": start_col, "endColumnIndex": end_col},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": '0.00"%"'}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    }]

def color_positive_negative(ws_id, rn, col_idx, val):
    try:
        val_f = float(val)
        if val_f > 0:
            return color_cell_req(ws_id, rn, col_idx, "d9ead3", "0b8043")
        elif val_f < 0:
            return color_cell_req(ws_id, rn, col_idx, "fde9d9", "c62828")
        else:
            return color_cell_req(ws_id, rn, col_idx, "f1f1f1", "666666")
    except:
        return None

def color_action_signal(ws_id, rn, col_idx, action):
    # Combines GITHUB DATA Final Action colors and Portfolio Signal colors
    ACTION_COLORS = {
        # GITHUB DATA
        "STRONG BUY":  ("00c853", "ffffff"),
        "BUY":         ("0b8043", "ffffff"),
        "ACCUMULATE":  ("d9ead3", "0b8043"),
        "HOLD":        ("fff2cc", "7f4f00"),
        "WATCH":       ("fce8b2", "7f4f00"),
        "AVOID":       ("fde9d9", "c62828"),
        "SELL":        ("cc0000", "ffffff"),
        # Portfolio Signals
        "BUY MORE":          ("0b8043", "ffffff"),
        "TARGET HIT - TRIM": ("d9ead3", "0b8043"),
        "SELL - SL HIT":     ("cc0000", "ffffff"),
    }
    if action in ACTION_COLORS:
        bg, fg = ACTION_COLORS[action]
        return color_cell_req(ws_id, rn, col_idx, bg, fg)
    return None

