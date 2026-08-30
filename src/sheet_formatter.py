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
from profiler import profiler



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
    "Strong Uptrend":   ("c6efce", "276221"),
    "Uptrend":          ("d9ead3", "0b8043"),
    "Weak Uptrend":     ("ebf3e8", "0b8043"),
    "Neutral":          ("f3f3f3", "555555"),
    "Weak Downtrend":   ("fef3c7", "92400e"),
    "Downtrend":        ("fde9d9", "c62828"),
    "Strong Downtrend": ("fce5cd", "b45309"),
}


# ══════════════════════════════════════════════
# GITHUB DATA ROW-LEVEL PALETTES
# Single source of truth for write_github_data()'s Final Action /
# Buying Zone / Buy-Sell Price Range cell colours — used by GITHUB DATA
# and (via the same write_github_data() call) Future Buy. Moved here
# from github_data_builder.py so they're inspectable/reusable instead
# of living inline inside one function.
#
# NOTE: these are deliberately namespaced GITHUB_DATA_* and are separate
# from color_action_signal()'s own ACTION_COLORS below (Portfolio Signal
# palette) and from growth_screener_builder.py / history_tracker.py's
# local ACTION_COLORS — those use different colour/threshold choices for
# their own tabs and are intentionally left as-is.
# ══════════════════════════════════════════════
GITHUB_DATA_ACTION_COLORS = {
    "STRONG BUY":  ("c6efce", "276221"),  # strong green — light
    "BUY":         ("d9ead3", "0b8043"),  # light green
    "ACCUMULATE":  ("ebf3e8", "0b8043"),  # very light green
    "HOLD":        ("fff2cc", "7f4f00"),
    "WATCH":       ("fce8b2", "7f4f00"),
    "AVOID":       ("fde9d9", "c62828"),
    "SELL":        ("fce5cd", "b45309"),  # light orange — not dark red+white
}

GITHUB_DATA_BUYING_ZONE_COLORS = {
    "🟢🟢 ADD AGGRESSIVELY": ("c6efce", "276221"),  # strong light green
    "🟢 ACCUMULATE":         ("d9ead3", "0b8043"),  # light green
    "🟡 SMALL BUY":          ("fef3c7", "92400e"),  # light amber
    "🔎 INVESTIGATE WHY":    ("fce5cd", "b45309"),  # light orange — not dark red/saturated
    "❌ WAIT":               ("fde9d9", "c62828"),  # light red/pink
}

# Light-tint colour map for Buy/Sell Price Range — keyed by buying zone,
# defined once, reused per row.
GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS = {
    "🟢🟢 ADD AGGRESSIVELY": ("c8f5dc", "0b5e2a"),
    "🔎 INVESTIGATE WHY":    ("fde3cc", "b84000"),
    "🟢 ACCUMULATE":         ("eaf5e8", "0b5e2a"),
    "🟡 SMALL BUY":          ("fdf9e3", "7f4f00"),
    "❌ WAIT":               ("fef2f0", "c62828"),
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


def color_cell_req(sheet_id, row_idx, col_idx, bg, fg, bold=True, font_size=None):
    fmt = {
        "backgroundColor": hex_rgb(bg),
        "textFormat": {"foregroundColor": hex_rgb(fg), "bold": bold}
    }
    if font_size:
        fmt["textFormat"]["fontSize"] = font_size
        
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                "startColumnIndex": col_idx, "endColumnIndex": col_idx + 1
            },
            "cell": {"userEnteredFormat": fmt},
            "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.textFormat"
        }
    }
def clear_all_formatting_reqs(ws_id):
    """Reset every cell's formatting to default across the whole sheet.
    ws.clear() only wipes values, not backgrounds/borders/fonts — call
    this first so no formatting survives from a previous schema/writer.
    Also unmerges every cell first: a leftover merge from a previous run
    (e.g. one written with a different column count) otherwise survives
    ws.clear()/repeatCell and makes the next mergeCells request fail with
    "You must select all cells in a merged range to merge or unmerge them"
    whenever this run's group-header ranges don't line up with the old
    merge boundaries exactly."""
    return [{
        "unmergeCells": {
            "range": {"sheetId": ws_id}
        }
    }, {
        "repeatCell": {
            "range": {"sheetId": ws_id},
            "cell": {"userEnteredFormat": {}},
            "fields": "userEnteredFormat"
        }
    }]

def get_group_header_merge_reqs(ws_id, group_ranges, frozen_cols=0, row_idx=0):
    """group_ranges: list of (start_col_idx, end_col_idx_inclusive, label).
    Merges each group's columns in row_idx and styles that row as a
    dark-blue banner sitting above the normal column-name header row.
    frozen_cols: number of frozen columns (matches freeze_cols passed to
    get_structural_format_reqs for the same sheet). The Sheets API refuses
    to merge a range that spans across the frozen/non-frozen boundary, so
    any group crossing it is merged as two pieces: the lone frozen cell
    (left as-is, no merge needed for a single cell) and the remaining
    unfrozen columns (merged, carries the label)."""
    reqs = []
    for start_col, end_col, label in group_ranges:
        merge_start = frozen_cols if (start_col < frozen_cols <= end_col) else start_col
        if end_col > merge_start:
            reqs.append({
                "mergeCells": {
                    "range": {
                        "sheetId": ws_id,
                        "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                        "startColumnIndex": merge_start, "endColumnIndex": end_col + 1
                    },
                    "mergeType": "MERGE_ALL"
                }
            })
    last_col = group_ranges[-1][1] + 1
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                      "startColumnIndex": 0, "endColumnIndex": last_col},
            "cell": {"userEnteredFormat": {
                "backgroundColor": hex_rgb("1f4e78"),
                "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 9},
                "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                "wrapStrategy": "WRAP"
            }},
            "fields": "userEnteredFormat"
        }
    })
    reqs.append({
        "updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "ROWS", "startIndex": row_idx, "endIndex": row_idx + 1},
            "properties": {"pixelSize": 30}, "fields": "pixelSize"
        }
    })
    return reqs

def get_group_header_color_reqs(ws_id, group_ranges, frozen_cols=0, row_idx=0):
    """
    Apply differentiated colors to each group header in row_idx.
    Subtle pastel palette to visually distinguish group boundaries.
    group_ranges: list of (start_col_idx, end_col_idx_inclusive, label)
    """
    # Pastel color palette for 8 groups: (background, text)
    GROUP_COLORS = [
        ("cfe2f3", "1f4e78"),  # Group 1: light blue
        ("e2d7f3", "5b2c6f"),  # Group 2: light purple
        ("fef5d9", "7f6000"),  # Group 3: light yellow
        ("d9f0d3", "1b5e20"),  # Group 4: light green
        ("d0e8e8", "004d40"),  # Group 5: light teal
        ("ffe8d1", "b8860b"),  # Group 6: light orange
        ("e8e8f0", "424242"),  # Group 7: light blue-grey
        ("f3e5f5", "6a1b9a"),  # Group 8: light lavender
    ]
    
    reqs = []
    for idx, (start_col, end_col, label) in enumerate(group_ranges):
        if idx >= len(GROUP_COLORS):
            # Fallback to default dark blue if more than 8 groups
            bg_color = "1f4e78"
            fg_color = "ffffff"
        else:
            bg_color, fg_color = GROUP_COLORS[idx]
        
        # Determine merge start (accounting for frozen columns)
        merge_start = frozen_cols if (start_col < frozen_cols <= end_col) else start_col
        
        # Apply color to the full group range (will show in merged cells)
        reqs.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws_id,
                    "startRowIndex": row_idx, "endRowIndex": row_idx + 1,
                    "startColumnIndex": start_col, "endColumnIndex": end_col + 1
                },
                "cell": {"userEnteredFormat": {
                    "backgroundColor": hex_rgb(bg_color),
                    "textFormat": {"foregroundColor": hex_rgb(fg_color), "bold": True, "fontSize": 9},
                    "horizontalAlignment": "CENTER", "verticalAlignment": "MIDDLE",
                    "wrapStrategy": "WRAP"
                }},
                "fields": "userEnteredFormat"
            }
        })
    
    return reqs

def get_structural_format_reqs(ws_id, num_rows, num_cols, widths=None, freeze_rows=1, freeze_cols=1, header_row_idx=0):
    profiler.increment("Formatting operations")
    reqs = []
    # Header format (column-name row — may sit at row 0 or lower if a
    # merged group-header row is stacked above it)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": header_row_idx, "endRowIndex": header_row_idx + 1,
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
    # Row height for header row
    reqs.append({
        "updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "ROWS", "startIndex": header_row_idx, "endIndex": header_row_idx + 1},
            "properties": {"pixelSize": 50}, "fields": "pixelSize"
        }
    })
    # Column widths
    if widths:
        reqs += [{"updateDimensionProperties": {
            "range": {"sheetId": ws_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"
        }} for i, w in enumerate(widths)]

    # Alternating row colors — start right after the header row
    data_start = header_row_idx + 1
    for i in range(num_rows):
        rn = data_start + i
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

def get_true_percentage_format_reqs(ws_id, start_row, end_row, start_col, end_col):
    return [{
        "repeatCell": {
            "range": {"sheetId": ws_id, "startRowIndex": start_row, "endRowIndex": end_row,
                      "startColumnIndex": start_col, "endColumnIndex": end_col},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "PERCENTAGE", "pattern": "0.00%"}}},
            "fields": "userEnteredFormat.numberFormat"
        }
    }]

def color_positive_negative(ws_id, rn, col_idx, val):
    try:
        val_f = float(val)
        if val_f > 0:
            return color_cell_req(ws_id, rn, col_idx, "d9ead3", "0b8043", bold=False)
        elif val_f < 0:
            return color_cell_req(ws_id, rn, col_idx, "fde9d9", "c62828", bold=False)
        else:
            return color_cell_req(ws_id, rn, col_idx, "f1f1f1", "666666", bold=False)
    except:
        return None

def color_action_signal(ws_id, rn, col_idx, action):
    # Combines GITHUB DATA Final Action colors and Portfolio Signal colors
    ACTION_COLORS = {
        # GITHUB DATA
        "STRONG BUY":  ("00c853", "ffffff"),
        "BUY":         ("0b8043", "ffffff"),
        "ACCUMULATE":  ("d9ead3", "0b8043"),
        "HOLD":        ("d9ead3", "0b8043"),
        "WATCH":       ("fce8b2", "7f4f00"),
        "AVOID":       ("fde9d9", "c62828"),
        "SELL":        ("cc0000", "ffffff"),
        # Portfolio Signals
        "BUY MORE":          ("e8f0fe", "1967d2"),
        "TARGET HIT - TRIM": ("d9ead3", "0b8043"),
        "SELL - SL HIT":     ("fde9d9", "c62828"),
    }
    if action in ACTION_COLORS:
        bg, fg = ACTION_COLORS[action]
        return color_cell_req(ws_id, rn, col_idx, bg, fg, bold=False)
    return None

def get_weight_gradient_rule(ws_id, start_row, end_row, col_idx):
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": ws_id,
                    "startRowIndex": start_row,
                    "endRowIndex": end_row,
                    "startColumnIndex": col_idx,
                    "endColumnIndex": col_idx + 1
                }],
                "gradientRule": {
                    "minpoint": {
                        "color": {"red": 0.34, "green": 0.73, "blue": 0.54},
                        "type": "NUMBER",
                        "value": "0"
                    },
                    "midpoint": {
                        "color": {"red": 1.0, "green": 0.88, "blue": 0.51},
                        "type": "NUMBER",
                        "value": "2.5"
                    },
                    "maxpoint": {
                        "color": {"red": 0.9, "green": 0.49, "blue": 0.45},
                        "type": "NUMBER",
                        "value": "5"
                    }
                }
            },
            "index": 0
        }
    }


def build_github_data_format_requests(ws_id, rows, start_row=0, freeze_rows=2, freeze_cols=1):
    """
    CANONICAL FORMATTING ENGINE FOR 41-COLUMN GITHUB-DATA-STYLE TABLES.
    Used identically by GITHUB DATA, Future Buy, and any watchlist tab.

    start_row: Row index where the table starts.
               - If 0 (direct table): group header is at row 0, column headers at row 1, data starts at row 2.
               - If >0 (e.g. 13 below a Top 10 block): group header is at row start_row, column headers at start_row + 1, data starts at start_row + 2.
    """
    from github_data_builder import (
        GITHUB_DATA_COLS, GITHUB_DATA_HEADER_NAMES, GITHUB_DATA_COL_WIDTHS,
        GROUP_DEFS
    )
    C = GITHUB_DATA_COLS
    num_cols = len(C)
    widths = [GITHUB_DATA_COL_WIDTHS.get(key, 70) for key in C]

    FROZEN_COLS = freeze_cols
    group_ranges = [(C[sk], C[ek], label) for sk, ek, label in GROUP_DEFS]

    group_hdr_idx = start_row
    col_hdr_idx = start_row + 1
    data_start_idx = start_row + 2
    num_rows = len(rows)

    reqs = []

    # 1. Structural formatting & Column widths & Alternating row backgrounds
    reqs += get_structural_format_reqs(
        ws_id, num_rows, num_cols, widths,
        freeze_rows=freeze_rows, freeze_cols=FROZEN_COLS,
        header_row_idx=col_hdr_idx
    )

    # 2. Group header merges, pastel background colors, and height 30px
    reqs += get_group_header_merge_reqs(ws_id, group_ranges, frozen_cols=FROZEN_COLS, row_idx=group_hdr_idx)
    reqs += get_group_header_color_reqs(ws_id, group_ranges, frozen_cols=FROZEN_COLS, row_idx=group_hdr_idx)

    # 3. Number formats for percentage and currency columns
    pct_cols = [C["day_chg_pct"], C["return_1w"], C["return_1m"], C["div"], C["roe"], C["roa"], C["rev_growth"]]
    for col_idx in pct_cols:
        reqs += get_percentage_format_reqs(ws_id, data_start_idx, data_start_idx + num_rows, col_idx, col_idx + 1)

    curr_cols = [C["low52"], C["cmp"], C["high52"], C["eps"], C["bv"]]
    for col_idx in curr_cols:
        reqs += get_currency_format_reqs(ws_id, data_start_idx, data_start_idx + num_rows, col_idx, col_idx + 1)

    # Helper to parse float values safely
    def sf(row, key):
        idx = C[key]
        try:
            v = str(row[idx]).replace("%", "").replace(",", "").replace("₹", "").replace(" Cr", "").strip()
            return float(v) if len(row) > idx and v else None
        except Exception:
            return None

    # 4. Cell-level colorings for every row
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

        # Market-cap tier family: Symbol, Mkt Cap Cr
        mcap_tier_v = sf(row, "mcap")
        if mcap_tier_v is not None:
            if mcap_tier_v >= 25000:     cb, cf = "d9ead3", "0b8043"   # Large Cap
            elif mcap_tier_v >= 5000:    cb, cf = "d9eaf7", "1565c0"   # Mid Cap
            else:                        cb, cf = "fde9d9", "c62828"  # Small Cap
            for key in ("symbol", "mcap"):
                reqs.append(color_cell_req(ws_id, rn, C[key], cb, cf))

        # 52W High / Low
        reqs.append(color_cell_req(ws_id, rn, C["high52"], "eaf4fb", "1565c0", bold=False))
        reqs.append(color_cell_req(ws_id, rn, C["low52"], "fdf2f2", "c62828", bold=False))

        # Day Chg%
        day_chg_v = sf(row, "day_chg_pct")
        if day_chg_v is not None:
            if day_chg_v > 0:
                reqs.append(color_cell_req(ws_id, rn, C["day_chg_pct"], "d9ead3", "0b8043"))
            elif day_chg_v < 0:
                reqs.append(color_cell_req(ws_id, rn, C["day_chg_pct"], "fde9d9", "c62828"))
            else:
                reqs.append(color_cell_req(ws_id, rn, C["day_chg_pct"], "f1f1f1", "666666"))

        # 1W Return %
        ret1w_v = sf(row, "return_1w")
        if ret1w_v is not None:
            if ret1w_v > 0:
                reqs.append(color_cell_req(ws_id, rn, C["return_1w"], "d9ead3", "0b8043"))
            elif ret1w_v < 0:
                reqs.append(color_cell_req(ws_id, rn, C["return_1w"], "fde9d9", "c62828"))
            else:
                reqs.append(color_cell_req(ws_id, rn, C["return_1w"], "f1f1f1", "666666"))

        # 1M Return %
        ret1m_v = sf(row, "return_1m")
        if ret1m_v is not None:
            if ret1m_v > 0:
                reqs.append(color_cell_req(ws_id, rn, C["return_1m"], "d9ead3", "0b8043"))
            elif ret1m_v < 0:
                reqs.append(color_cell_req(ws_id, rn, C["return_1m"], "fde9d9", "c62828"))
            else:
                reqs.append(color_cell_req(ws_id, rn, C["return_1m"], "f1f1f1", "666666"))

        # Buy 20% Less (% from 52W high)
        pct_high_v = sf(row, "pct_high")
        if pct_high_v is not None:
            if pct_high_v >= -20:
                reqs.append(color_cell_req(ws_id, rn, C["pct_high"], "d9ead3", "0b8043"))
            else:
                reqs.append(color_cell_req(ws_id, rn, C["pct_high"], "fde9d9", "c62828"))

        # PE
        if pe_v is not None:
            if 0 < pe_v <= 25:   reqs.append(color_cell_req(ws_id, rn, C["pe"], "d9ead3", "0b8043"))
            elif pe_v <= 40:     reqs.append(color_cell_req(ws_id, rn, C["pe"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["pe"], "fde9d9", "c62828"))

        # EPS
        if eps_v is not None:
            reqs.append(color_cell_req(ws_id, rn, C["eps"], "d9ead3", "0b8043") if eps_v > 0
                        else color_cell_req(ws_id, rn, C["eps"], "fde9d9", "c62828"))

        # P/B
        if pb_v is not None:
            if pb_v <= 3:        reqs.append(color_cell_req(ws_id, rn, C["pb"], "d9ead3", "0b8043"))
            elif pb_v <= 5:      reqs.append(color_cell_req(ws_id, rn, C["pb"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["pb"], "fde9d9", "c62828"))

        # Div Yield%
        if div_v is not None:
            if div_v >= 2:       reqs.append(color_cell_req(ws_id, rn, C["div"], "d9ead3", "0b8043"))
            elif div_v >= 1:     reqs.append(color_cell_req(ws_id, rn, C["div"], "fff2cc", "7f4f00"))

        # RSI
        if rsi_v is not None:
            if   rsi_v < 35:  reqs.append(color_cell_req(ws_id, rn, C["rsi"], "d9ead3", "0b8043"))
            elif rsi_v > 70:  reqs.append(color_cell_req(ws_id, rn, C["rsi"], "fde9d9", "c62828"))
            elif rsi_v > 60:  reqs.append(color_cell_req(ws_id, rn, C["rsi"], "fff2cc", "7f4f00"))

        # ROE% / ROA% / Debt-Equity / Rev Growth% / Beta
        if roe_v is not None:
            if roe_v >= 15:      reqs.append(color_cell_req(ws_id, rn, C["roe"], "d9ead3", "0b8043"))
            elif roe_v >= 8:     reqs.append(color_cell_req(ws_id, rn, C["roe"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["roe"], "fde9d9", "c62828"))
        if roa_v is not None:
            if roa_v >= 2:       reqs.append(color_cell_req(ws_id, rn, C["roa"], "d9ead3", "0b8043"))
            elif roa_v >= 1:     reqs.append(color_cell_req(ws_id, rn, C["roa"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["roa"], "fde9d9", "c62828"))
        if debt_v is not None:
            if debt_v <= 0.5:    reqs.append(color_cell_req(ws_id, rn, C["debt_eq"], "d9ead3", "0b8043"))
            elif debt_v <= 1:    reqs.append(color_cell_req(ws_id, rn, C["debt_eq"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["debt_eq"], "fde9d9", "c62828"))
        if growth_v is not None:
            if growth_v >= 10:   reqs.append(color_cell_req(ws_id, rn, C["rev_growth"], "d9ead3", "0b8043"))
            elif growth_v >= 0:  reqs.append(color_cell_req(ws_id, rn, C["rev_growth"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["rev_growth"], "fde9d9", "c62828"))
        if beta_v is not None:
            if beta_v <= 1:      reqs.append(color_cell_req(ws_id, rn, C["beta"], "d9ead3", "0b8043"))
            elif beta_v <= 1.5:  reqs.append(color_cell_req(ws_id, rn, C["beta"], "fff2cc", "7f4f00"))
            else:                reqs.append(color_cell_req(ws_id, rn, C["beta"], "fde9d9", "c62828"))

        # Strengths / Weaknesses
        reqs.append(color_cell_req(ws_id, rn, C["strengths"], "f1f9f1", "0b8043", bold=False))
        reqs.append(color_cell_req(ws_id, rn, C["weaknesses"], "fdf2f2", "c62828", bold=False))

        # Technical Setup / CMP / Fair Val / Final Action / Risk Level
        if tech_set in TECHNICAL_SETUP_COLORS:
            bg, fg = TECHNICAL_SETUP_COLORS[tech_set]
            reqs.append(color_cell_req(ws_id, rn, C["technical_setup"], bg, fg))
        reqs.append(color_cell_req(ws_id, rn, C["cmp"], "f1f8e9", "33691e", bold=False))
        reqs.append(color_cell_req(ws_id, rn, C["fair_val"], "e8f5e9", "1b5e20", bold=False))
        if action in GITHUB_DATA_ACTION_COLORS:
            bg_a, fg_a = GITHUB_DATA_ACTION_COLORS[action]
            reqs.append(color_cell_req(ws_id, rn, C["action"], bg_a, fg_a))
        if b_zone in GITHUB_DATA_BUYING_ZONE_COLORS:
            bg_b, fg_b = GITHUB_DATA_BUYING_ZONE_COLORS[b_zone]
            reqs.append(color_cell_req(ws_id, rn, C["buying_zone"], bg_b, fg_b))
            if b_zone in GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS:
                lbg, lfg = GITHUB_DATA_PRICE_RANGE_LIGHT_COLORS[b_zone]
                reqs.append(color_cell_req(ws_id, rn, C["price_range"], lbg, lfg, bold=False))

        if risk_val:
            if risk_val == "Very High": reqs.append(color_cell_req(ws_id, rn, C["econ_sens"], "fde9d9", "c62828"))
            elif risk_val in ("Medium-High", "High"): reqs.append(color_cell_req(ws_id, rn, C["econ_sens"], "ffe599", "7f4f00"))
            elif risk_val == "Medium": reqs.append(color_cell_req(ws_id, rn, C["econ_sens"], "fff2cc", "7f4f00"))
            elif risk_val in ("Low", "Low-Medium"): reqs.append(color_cell_req(ws_id, rn, C["econ_sens"], "d9ead3", "0b8043"))

        # Quality / Valuation / Timing / Total
        if q_sc is not None:
            if q_sc >= 30:   reqs.append(color_cell_req(ws_id, rn, C["quality"], "d9ead3", "0b8043"))
            elif q_sc <= 15: reqs.append(color_cell_req(ws_id, rn, C["quality"], "fde9d9", "c62828"))
        if v_sc is not None:
            if v_sc >= 22:   reqs.append(color_cell_req(ws_id, rn, C["valuation"], "d9ead3", "0b8043"))
            elif v_sc <= 10: reqs.append(color_cell_req(ws_id, rn, C["valuation"], "fde9d9", "c62828"))
        if t_sc is not None:
            if t_sc >= 22:   reqs.append(color_cell_req(ws_id, rn, C["timing"], "d9ead3", "0b8043"))
            elif t_sc <= 10: reqs.append(color_cell_req(ws_id, rn, C["timing"], "fde9d9", "c62828"))
        if tot_sc is not None:
            if   tot_sc >= 65: reqs.append(color_cell_req(ws_id, rn, C["total"], "00c853", "ffffff"))
            elif tot_sc >= 50: reqs.append(color_cell_req(ws_id, rn, C["total"], "d9ead3", "0b8043"))
            elif tot_sc >= 35: reqs.append(color_cell_req(ws_id, rn, C["total"], "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws_id, rn, C["total"], "fde9d9", "c62828"))

        # Vol Spike / Trend
        if vol_v is not None:
            if vol_v >= 2:      reqs.append(color_cell_req(ws_id, rn, C["vol_spike"], "fde9d9", "c62828"))
            elif vol_v >= 1.5:  reqs.append(color_cell_req(ws_id, rn, C["vol_spike"], "fff2cc", "7f4f00"))
            else:               reqs.append(color_cell_req(ws_id, rn, C["vol_spike"], "d9ead3", "0b8043"))
        if trend_val in TREND_COLORS:
            bg, fg = TREND_COLORS[trend_val]
            reqs.append(color_cell_req(ws_id, rn, C["trend"], bg, fg))

        # AI News columns
        news_sent = str(row[C["news_sentiment"]]).strip() if len(row) > C["news_sentiment"] else ""
        if "Bullish" in news_sent:
            reqs.append(color_cell_req(ws_id, rn, C["news_sentiment"], "d9ead3", "0b8043"))
        elif "Bearish" in news_sent:
            reqs.append(color_cell_req(ws_id, rn, C["news_sentiment"], "fde9d9", "c62828"))
        elif news_sent:
            reqs.append(color_cell_req(ws_id, rn, C["news_sentiment"], "f1f1f1", "555555"))

        reqs.append(color_cell_req(ws_id, rn, C["news_summary"], "e8f5f9", "01579b", bold=False))
        reqs.append(color_cell_req(ws_id, rn, C["news_source"],  "f5f5f5", "757575", bold=False))

    # 5. Default filter
    reqs.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": ws_id,
                    "startRowIndex": col_hdr_idx,
                    "endRowIndex": data_start_idx + num_rows,
                    "startColumnIndex": 0,
                    "endColumnIndex": num_cols,
                }
            }
        }
    })

    return reqs
