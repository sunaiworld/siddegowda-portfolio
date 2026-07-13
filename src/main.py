#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Daily Auto-Updater
Sector-Aware Unified Scoring Engine v2.0
GitHub Actions — runs daily 6 PM IST
"""

import os
import json
import time
import logging
import statistics
import requests
import math
from datetime import datetime, date

import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# ══════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════
SHEET_ID         = os.environ.get("SHEET_ID", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SCOPES           = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
BATCH_SIZE  = 5
SLEEP_BATCH = 8
SLEEP_INFO  = 3
SL_PCT      = 0.07
TARGET_PCT  = 0.20

# ══════════════════════════════════════════════
# WATCHLISTS
# Symbol-only categories: no qty/buy-price/date.
# Displayed as market-data-only tabs (reuse GITHUB DATA
# tab logic). Add new watchlist categories here instead
# of hardcoding values elsewhere.
# ══════════════════════════════════════════════
WATCHLISTS = {
    "Future Buy": [
        "ZYDUSLIFE", "LUPIN", "SUNPHARMA", "ADANIENT", "ADANIPORTS",
        "OFSS", "AUBANK", "AUROPHARMA", "GLAND", "DRREDDY",
        "DIVISLAB", "BIOCON", "AJANTPHARM", "MARICO", "BHARATFORG",
        "ICICIBANK", "POLYCAB", "AXISBANK", "CGPOWER", "HONAUT",
        "GLENMARK", "CUMMINSIND", "LT", "SIEMENS", "EICHERMOT",
        "BEL", "NHPC", "FORTIS", "BHARTIARTL", "CIPLA",
        "COALINDIA", "HAL", "ADANIPOWER", "MOTILALOFS", "BRITANNIA",
        "NTPC", "BSE", "MRF", "DMART", "OIL",
        "ONGC", "NATIONALUM", "HINDPETRO", "BPCL", "BLUESTARCO",
        "DABUR", "MAZDOCK", "ABBOTINDIA", "TECHM", "MPHASIS",
        "MUTHOOTFIN", "COFORGE", "HINDZINC", "HAVELLS", "COCHINSHIP",
        "GLAXO", "JWL", "HINDCOPPER", "HCLTECH", "KPITTECH",
        "MEDPLUS", "ALKYLAMINE", "LAURUSLABS", "TTKPRESTIG", "JYOTHYLAB",
        "JKPAPER", "MASTEK", "WOCKPHARMA", "DATAPATTNS",
    ],
}

# ══════════════════════════════════════════════
# SECTOR ARCHETYPE MAP
# Maps yfinance industry/sector strings → archetype key
# ══════════════════════════════════════════════
SECTOR_ARCHETYPE_MAP = {
    # Banks
    "banks": "FINANCIAL_BANK",
    "bank": "FINANCIAL_BANK",
    "banks - regional": "FINANCIAL_BANK",
    "banks - diversified": "FINANCIAL_BANK",
    "regional banks": "FINANCIAL_BANK",
    # NBFCs / Credit
    "credit services": "FINANCIAL_NBFC",
    "nbfc": "FINANCIAL_NBFC",
    "mortgage finance": "FINANCIAL_NBFC",
    "consumer finance": "FINANCIAL_NBFC",
    "thrifts & mortgage finance": "FINANCIAL_NBFC",
    "diversified financials": "FINANCIAL_NBFC",
    "financial conglomerates": "FINANCIAL_NBFC",
    # Insurance
    "insurance": "FINANCIAL_INSURANCE",
    "insurance - life": "FINANCIAL_INSURANCE",
    "insurance - diversified": "FINANCIAL_INSURANCE",
    "insurance brokers": "FINANCIAL_INSURANCE",
    "insurance - property & casualty": "FINANCIAL_INSURANCE",
    # Capital Markets
    "capital markets": "FINANCIAL_CAPITAL_MARKETS",
    "asset management": "FINANCIAL_CAPITAL_MARKETS",
    "financial data & stock exchanges": "FINANCIAL_CAPITAL_MARKETS",
    "investment banking & brokerage": "FINANCIAL_CAPITAL_MARKETS",
    # IT / Software
    "software": "QUALITY_GROWTH",
    "software - application": "QUALITY_GROWTH",
    "software - infrastructure": "QUALITY_GROWTH",
    "information technology services": "QUALITY_GROWTH",
    "technology": "QUALITY_GROWTH",
    # Pharma / Healthcare
    "drug manufacturers - general": "QUALITY_GROWTH",
    "drug manufacturers - specialty & generic": "QUALITY_GROWTH",
    "pharmaceuticals": "QUALITY_GROWTH",
    "biotechnology": "QUALITY_GROWTH",
    "medical devices": "QUALITY_GROWTH",
    "medical care facilities": "QUALITY_GROWTH",
    "diagnostics & research": "QUALITY_GROWTH",
    "healthcare": "QUALITY_GROWTH",
    # Specialty Chemicals
    "specialty chemicals": "QUALITY_GROWTH",
    "chemicals": "QUALITY_GROWTH",
    # FMCG / Consumer Staples
    "consumer defensive": "CONSUMER_STAPLES",
    "beverages - non-alcoholic": "CONSUMER_STAPLES",
    "beverages - alcoholic": "CONSUMER_STAPLES",
    "packaged foods": "CONSUMER_STAPLES",
    "household & personal products": "CONSUMER_STAPLES",
    "food distribution": "CONSUMER_STAPLES",
    "tobacco": "CONSUMER_STAPLES",
    "fmcg": "CONSUMER_STAPLES",
    "personal care products": "CONSUMER_STAPLES",
    # Consumer Discretionary / Auto
    "consumer cyclical": "CONSUMER_DISCRETIONARY",
    "auto manufacturers": "CONSUMER_DISCRETIONARY",
    "auto parts": "CONSUMER_DISCRETIONARY",
    "automotive": "CONSUMER_DISCRETIONARY",
    "specialty retail": "CONSUMER_DISCRETIONARY",
    "retail": "CONSUMER_DISCRETIONARY",
    "restaurants": "CONSUMER_DISCRETIONARY",
    "leisure": "CONSUMER_DISCRETIONARY",
    "apparel manufacturing": "CONSUMER_DISCRETIONARY",
    "apparel retail": "CONSUMER_DISCRETIONARY",
    "consumer electronics": "CONSUMER_DISCRETIONARY",
    "home improvement retail": "CONSUMER_DISCRETIONARY",
    # Industrial / Capital Goods / Infrastructure
    "industrials": "INDUSTRIAL_CAPEX",
    "industrial conglomerates": "INDUSTRIAL_CAPEX",
    "specialty industrial machinery": "INDUSTRIAL_CAPEX",
    "electrical equipment & parts": "INDUSTRIAL_CAPEX",
    "engineering & construction": "INDUSTRIAL_CAPEX",
    "construction": "INDUSTRIAL_CAPEX",
    "infrastructure": "INDUSTRIAL_CAPEX",
    "utilities": "INDUSTRIAL_CAPEX",
    "utilities - regulated electric": "INDUSTRIAL_CAPEX",
    "utilities - renewable": "INDUSTRIAL_CAPEX",
    "power": "INDUSTRIAL_CAPEX",
    "cement": "INDUSTRIAL_CAPEX",
    "building materials": "INDUSTRIAL_CAPEX",
    # Commodity / Cyclical
    "basic materials": "COMMODITY_CYCLICAL",
    "steel": "COMMODITY_CYCLICAL",
    "aluminum": "COMMODITY_CYCLICAL",
    "copper": "COMMODITY_CYCLICAL",
    "metals & mining": "COMMODITY_CYCLICAL",
    "other industrial metals & mining": "COMMODITY_CYCLICAL",
    "oil & gas integrated": "COMMODITY_CYCLICAL",
    "oil & gas refining & marketing": "COMMODITY_CYCLICAL",
    "oil & gas e&p": "COMMODITY_CYCLICAL",
    "energy": "COMMODITY_CYCLICAL",
    "coal": "COMMODITY_CYCLICAL",
    "fertilizers & agricultural chemicals": "COMMODITY_CYCLICAL",
    "agricultural inputs": "COMMODITY_CYCLICAL",
}

# Symbol-level overrides for stocks that yfinance misclassifies
SYMBOL_ARCHETYPE_OVERRIDE = {
    "HDFCBANK": "FINANCIAL_BANK",
    "KOTAKBANK": "FINANCIAL_BANK",
    "AUBANK": "FINANCIAL_BANK",
    "BANDHANBNK": "FINANCIAL_BANK",
    "IDFCFIRSTB": "FINANCIAL_BANK",
    "CUB": "FINANCIAL_BANK",
    "KTKBANK": "FINANCIAL_BANK",
    "INDUSINDBK": "FINANCIAL_BANK",
    "EQUITASBNK": "FINANCIAL_BANK",
    "UJJIVANSFB": "FINANCIAL_BANK",
    "PNB": "FINANCIAL_BANK",
    "CANBK": "FINANCIAL_BANK",
    "BAJFINANCE": "FINANCIAL_NBFC",
    "BAJAJFINSV": "FINANCIAL_NBFC",
    "CHOLAFIN": "FINANCIAL_NBFC",
    "FIVESTAR": "FINANCIAL_NBFC",
    "ARMANFIN": "FINANCIAL_NBFC",
    "APTUS": "FINANCIAL_NBFC",
    "AAVAS": "FINANCIAL_NBFC",
    "JIOFIN": "FINANCIAL_NBFC",
    "MANAPPURAM": "FINANCIAL_NBFC",
    "MUTHOOTFIN": "FINANCIAL_NBFC",
    "BAJAJHLDNG": "FINANCIAL_NBFC",
    "HDFCLIFE": "FINANCIAL_INSURANCE",
    "SBILIFE": "FINANCIAL_INSURANCE",
    "STARHEALTH": "FINANCIAL_INSURANCE",
    "POLICYBZR": "FINANCIAL_INSURANCE",
    "ANGELONE": "FINANCIAL_CAPITAL_MARKETS",
    "MOTILALOFS": "FINANCIAL_CAPITAL_MARKETS",
    "NUVAMA": "FINANCIAL_CAPITAL_MARKETS",
    "GEOJITFSL": "FINANCIAL_CAPITAL_MARKETS",
    "5PAISA": "FINANCIAL_CAPITAL_MARKETS",
    "CDSL": "FINANCIAL_CAPITAL_MARKETS",
    "CAMS": "FINANCIAL_CAPITAL_MARKETS",
    "HDFCAMC": "FINANCIAL_CAPITAL_MARKETS",
    "CRISIL": "FINANCIAL_CAPITAL_MARKETS",
    "IEX": "FINANCIAL_CAPITAL_MARKETS",
    "COALINDIA": "COMMODITY_CYCLICAL",
    "HINDPETRO": "COMMODITY_CYCLICAL",
    "VEDL": "COMMODITY_CYCLICAL",
    "HINDZINC": "COMMODITY_CYCLICAL",
    "CASTROLIND": "COMMODITY_CYCLICAL",
    "PETRONET": "COMMODITY_CYCLICAL",
    "RECLTD": "INDUSTRIAL_CAPEX",
    "ADANIPOWER": "INDUSTRIAL_CAPEX",
    "ITC": "CONSUMER_STAPLES",
    "COLPAL": "CONSUMER_STAPLES",
    "HINDUNILVR": "CONSUMER_STAPLES",
    "DABUR": "CONSUMER_STAPLES",
    "BRITANNIA": "CONSUMER_STAPLES",
    "NESTLEIND": "CONSUMER_STAPLES",
    "MARICO": "CONSUMER_STAPLES",
    "VBL": "CONSUMER_STAPLES",
    "PGHH": "CONSUMER_STAPLES",
    "BECTORFOOD": "CONSUMER_STAPLES",
    "AWL": "CONSUMER_STAPLES",
    "HEROMOTOCO": "CONSUMER_DISCRETIONARY",
    "TVSMOTOR": "CONSUMER_DISCRETIONARY",
    "MOTHERSON": "CONSUMER_DISCRETIONARY",
    "EXIDEIND": "CONSUMER_DISCRETIONARY",
    "TITAN": "CONSUMER_DISCRETIONARY",
    "KALYANKJIL": "CONSUMER_DISCRETIONARY",
    "TRENT": "CONSUMER_DISCRETIONARY",
    "PAGEIND": "CONSUMER_DISCRETIONARY",
    "SWIGGY": "CONSUMER_DISCRETIONARY",
    "PVRINOX": "CONSUMER_DISCRETIONARY",
    "SUNPHARMA": "QUALITY_GROWTH",
    "CIPLA": "QUALITY_GROWTH",
    "DRREDDY": "QUALITY_GROWTH",
    "NATCOPHARM": "QUALITY_GROWTH",
    "WOCKPHARMA": "QUALITY_GROWTH",
    "SUPRIYA": "QUALITY_GROWTH",
    "APOLLOHOSP": "QUALITY_GROWTH",
    "MAXHEALTH": "QUALITY_GROWTH",
    "NH": "QUALITY_GROWTH",
    "FORTIS": "QUALITY_GROWTH",
    "KIMS": "QUALITY_GROWTH",
    "LALPATHLAB": "QUALITY_GROWTH",
    "INDGN": "QUALITY_GROWTH",
    "MEDPLUS": "QUALITY_GROWTH",
    "AARTIIND": "QUALITY_GROWTH",
    "DEEPAKNTR": "QUALITY_GROWTH",
    "PIIND": "QUALITY_GROWTH",
    "PIDILITIND": "QUALITY_GROWTH",
    "INDIGOPNTS": "QUALITY_GROWTH",
    "TARSONS": "QUALITY_GROWTH",
    "DIXON": "QUALITY_GROWTH",
    "POLYCAB": "INDUSTRIAL_CAPEX",
    "TIINDIA": "INDUSTRIAL_CAPEX",
    "ARE&M": "INDUSTRIAL_CAPEX",
    "ACC": "INDUSTRIAL_CAPEX",
    "IRCTC": "CONSUMER_DISCRETIONARY",
}

# ══════════════════════════════════════════════
# SECTOR SCORING RULES
# Each archetype defines quality, valuation thresholds
# and which metrics to ignore
# ══════════════════════════════════════════════
SECTOR_RULES = {
    "FINANCIAL_BANK": {
        "ignore": ["debt_eq", "roce"],
        "quality": {
            "roa":        [(2.0, 15), (1.5, 12), (1.0, 8), (0.5, 4), (0, 0)],
            "roe":        [(18, 15),  (15, 12),  (12, 8),  (8, 4),   (0, 0)],
            "rev_growth": [(15, 10),  (10, 7),   (5, 4),   (0, 2),   (None, 0)],
        },
        "valuation": {
            "pb":  [(1.0, 20), (1.5, 15), (2.5, 10), (4.0, 5), (None, 0)],
            "div": [(2.0, 10), (1.0, 6),  (0.5, 3),  (None, 0)],
        },
    },
    "FINANCIAL_NBFC": {
        "ignore": ["debt_eq", "roce"],
        "quality": {
            "roe":        [(20, 20), (16, 15), (12, 10), (8, 5),  (0, 0)],
            "roa":        [(3.0, 12),(2.0, 9), (1.5, 6), (1.0, 3),(0, 0)],
            "rev_growth": [(20, 8),  (15, 6),  (10, 4),  (5, 2),  (None, 0)],
        },
        "valuation": {
            "pb":  [(1.5, 20), (2.5, 15), (4.0, 8), (None, 3)],
            "div": [(1.5, 10), (0.5, 5),  (None, 0)],
        },
    },
    "FINANCIAL_INSURANCE": {
        "ignore": ["debt_eq", "roce", "pe"],
        "quality": {
            "roe":        [(20, 20), (15, 15), (10, 8), (0, 0)],
            "rev_growth": [(20, 20), (15, 15), (10, 8), (5, 4), (None, 0)],
        },
        "valuation": {
            "pb":  [(2.0, 20), (3.5, 12), (5.0, 6), (None, 2)],
            "div": [(1.0, 10), (0.5, 5),  (None, 0)],
        },
    },
    "FINANCIAL_CAPITAL_MARKETS": {
        "ignore": ["debt_eq"],
        "quality": {
            "roe":        [(25, 20), (20, 15), (15, 10), (10, 5), (0, 0)],
            "roce":       [(25, 12), (20, 9),  (15, 6),  (0, 0)],
            "rev_growth": [(20, 8),  (10, 5),  (0, 2),   (None, 0)],
        },
        "valuation": {
            "pe":  [(20, 20), (30, 14), (40, 8), (None, 3)],
            "div": [(2.0, 10),(1.0, 5), (None, 0)],
        },
    },
    "QUALITY_GROWTH": {
        "ignore": [],
        "quality": {
            "roce":       [(25, 15), (20, 12), (15, 8), (10, 4), (0, 0)],
            "roe":        [(20, 10), (15, 8),  (12, 5), (8, 2),  (0, 0)],
            "rev_growth": [(20, 10), (15, 8),  (10, 5), (5, 2),  (None, 0)],
            "debt_eq":    [(0.2, 5), (0.5, 3), (1.0, 1),(None, 0)],
        },
        "valuation": {
            "pe":  [(25, 15), (35, 10), (50, 5), (None, 2)],
            "pb":  [(3.0, 10),(5.0, 7), (8.0, 4),(None, 1)],
            "div": [(1.5, 5), (0.5, 2), (None, 0)],
        },
    },
    "CONSUMER_STAPLES": {
        "ignore": ["debt_eq"],
        "quality": {
            "roce":       [(40, 20), (30, 16), (20, 10), (15, 5), (0, 0)],
            "roe":        [(30, 12), (20, 9),  (15, 6),  (0, 0)],
            "rev_growth": [(15, 8),  (10, 6),  (5, 3),   (None, 0)],
        },
        "valuation": {
            "pe":  [(35, 15), (50, 10), (65, 5), (None, 2)],
            "div": [(2.5, 15),(1.5, 10),(0.5, 5),(None, 0)],
        },
    },
    "CONSUMER_DISCRETIONARY": {
        "ignore": [],
        "quality": {
            "roce":       [(20, 15), (15, 12), (10, 7), (5, 3), (0, 0)],
            "roe":        [(18, 12), (14, 9),  (10, 5), (0, 0)],
            "rev_growth": [(15, 8),  (10, 6),  (5, 3),  (None, 0)],
            "debt_eq":    [(0.3, 5), (0.8, 3), (1.5, 1),(None, 0)],
        },
        "valuation": {
            "pe":  [(20, 18), (30, 12), (40, 6), (None, 2)],
            "pb":  [(3.0, 7), (5.0, 4), (None, 1)],
            "div": [(1.5, 5), (0.5, 2), (None, 0)],
        },
    },
    "INDUSTRIAL_CAPEX": {
        "ignore": [],
        "quality": {
            "roce":       [(15, 15), (12, 11), (8, 7),  (5, 3),  (0, 0)],
            "roe":        [(15, 12), (12, 9),  (8, 5),  (0, 0)],
            "rev_growth": [(20, 10), (15, 8),  (10, 5), (5, 2),  (None, 0)],
            "debt_eq":    [(1.0, 3), (2.0, 2), (3.0, 1),(None, 0)],
        },
        "valuation": {
            "pe":  [(20, 18), (30, 12), (40, 7), (None, 3)],
            "pb":  [(2.0, 7), (3.0, 4), (None, 1)],
            "div": [(2.0, 5), (1.0, 3), (None, 0)],
        },
    },
    "COMMODITY_CYCLICAL": {
        "ignore": [],
        "quality": {
            "roe":        [(20, 15), (15, 11), (10, 7), (5, 3), (0, 0)],
            "debt_eq":    [(0.3, 15),(0.8, 11),(1.5, 7),(2.5, 3),(None, 0)],
            "rev_growth": [(20, 10), (10, 7),  (0, 3),  (None, 0)],
        },
        "valuation": {
            "pe":  [(8, 10),  (12, 7),  (18, 4), (None, 1)],
            "pb":  [(1.0, 15),(1.5, 10),(2.5, 5),(None, 2)],
            "div": [(4.0, 5), (2.0, 3), (1.0, 1),(None, 0)],
        },
    },
    "DEFAULT": {
        "ignore": [],
        "quality": {
            "roe":        [(20, 15), (15, 11), (10, 6), (0, 0)],
            "roce":       [(20, 15), (15, 11), (10, 6), (0, 0)],
            "rev_growth": [(15, 10), (10, 7),  (5, 3),  (None, 0)],
        },
        "valuation": {
            "pe":  [(20, 15), (30, 10), (50, 5), (None, 2)],
            "pb":  [(2.0, 10),(4.0, 6), (None, 2)],
            "div": [(2.0, 5), (1.0, 3), (None, 0)],
        },
    },
}

# ══════════════════════════════════════════════
# ARCHETYPE DETECTION
# ══════════════════════════════════════════════
def get_archetype(sym, sector, industry):
    if sym.upper() in SYMBOL_ARCHETYPE_OVERRIDE:
        return SYMBOL_ARCHETYPE_OVERRIDE[sym.upper()]
    for key in [industry.lower(), sector.lower()]:
        if key in SECTOR_ARCHETYPE_MAP:
            return SECTOR_ARCHETYPE_MAP[key]
    return "DEFAULT"

# ══════════════════════════════════════════════
# UNIFIED SCORING ENGINE
# ══════════════════════════════════════════════
def score_metric(value, thresholds):
    if value is None:
        return 0
    for cutoff, pts in thresholds:
        if cutoff is None:
            return pts
        if value >= cutoff:
            return pts
    return 0

def score_debt(value, thresholds):
    if value is None:
        return 0
    for cutoff, pts in thresholds:
        if cutoff is None:
            return pts
        if value <= cutoff:
            return pts
    return 0

def compute_unified_score(sym, archetype, metrics):
    rules      = SECTOR_RULES.get(archetype, SECTOR_RULES["DEFAULT"])
    ignore     = rules.get("ignore", [])
    q_rules    = rules.get("quality", {})
    v_rules    = rules.get("valuation", {})

    quality_score    = 0
    valuation_score  = 0
    strengths        = []
    weaknesses       = []

    for metric, thresholds in q_rules.items():
        if metric in ignore:
            continue
        val = metrics.get(metric)
        if val is None:
            continue
        if metric == "debt_eq":
            pts = score_debt(val, thresholds)
            if pts >= 10:
                strengths.append(f"✓ Very low debt ({val:.1f}x)")
            elif pts >= 6:
                strengths.append(f"✓ Manageable debt ({val:.1f}x)")
            elif pts <= 1:
                weaknesses.append(f"✗ High debt ({val:.1f}x)")
        else:
            pts = score_metric(val, thresholds)
            label = metric.upper().replace("_", " ")
            if pts >= int(thresholds[0][1] * 0.8):
                strengths.append(f"✓ Strong {label} {val:.1f}%")
            elif pts <= int(thresholds[0][1] * 0.2) and pts > 0:
                weaknesses.append(f"✗ Weak {label} {val:.1f}%")
            elif pts == 0 and val is not None:
                weaknesses.append(f"✗ Poor {label} {val:.1f}%")
        quality_score += pts

    quality_score = min(quality_score, 40)

    for metric, thresholds in v_rules.items():
        if metric in ignore:
            continue
        val = metrics.get(metric)
        if val is None:
            continue
        if metric == "pe":
            if val <= 0:
                continue
            pts = 0
            for cutoff, p in thresholds:
                if cutoff is None:
                    pts = p
                    break
                if val <= cutoff:
                    pts = p
                    break
            if pts >= int(thresholds[0][1] * 0.8):
                strengths.append(f"✓ Attractive PE ({val:.1f}x)")
            elif pts <= int(thresholds[0][1] * 0.2):
                weaknesses.append(f"✗ Expensive PE ({val:.1f}x)")
        elif metric == "pb":
            pts = 0
            for cutoff, p in thresholds:
                if cutoff is None:
                    pts = p
                    break
                if val <= cutoff:
                    pts = p
                    break
            if pts >= int(thresholds[0][1] * 0.8):
                strengths.append(f"✓ Attractive P/B ({val:.1f}x)")
            elif pts <= int(thresholds[0][1] * 0.2):
                weaknesses.append(f"✗ Expensive P/B ({val:.1f}x)")
        elif metric == "div":
            pts = 0
            for cutoff, p in thresholds:
                if cutoff is None:
                    pts = p
                    break
                if val >= cutoff:
                    pts = p
                    break
            if pts >= int(thresholds[0][1] * 0.8):
                strengths.append(f"✓ Good dividend yield ({val:.1f}%)")
        else:
            pts = score_metric(val, thresholds)
        valuation_score += pts

    valuation_score = min(valuation_score, 30)

    rsi      = metrics.get("rsi")
    sma200   = metrics.get("sma200")
    cmp      = metrics.get("cmp")
    vol_spk  = metrics.get("vol_spike")
    cross    = metrics.get("cross", "")
    timing_score = 0

    if rsi is not None:
        if   rsi < 30:  timing_score += 15; strengths.append(f"✓ Deeply oversold RSI ({rsi})")
        elif rsi < 40:  timing_score += 10; strengths.append(f"✓ Oversold RSI ({rsi}) — good entry")
        elif rsi < 50:  timing_score += 5
        elif rsi < 60:  timing_score += 3
        elif rsi > 75:  weaknesses.append(f"✗ Overbought RSI ({rsi}) — wait")
        elif rsi > 70:  weaknesses.append(f"✗ High RSI ({rsi})")

    if sma200 and cmp:
        if cmp > sma200:
            timing_score += 10
        else:
            weaknesses.append("✗ Below 200-day MA — long-term trend broken")

    if cross == "Golden Cross":
        timing_score += 5
        strengths.append("✓ Golden Cross — momentum turning positive")
    elif cross == "Death Cross":
        timing_score = max(0, timing_score - 5)
        weaknesses.append("✗ Death Cross — momentum turning negative")

    if vol_spk and vol_spk > 2.0 and cmp and sma200 and cmp > sma200:
        timing_score += 5
        strengths.append(f"✓ Volume spike {vol_spk:.1f}x on uptrend")

    timing_score = min(max(timing_score, 0), 30)

    total_score = quality_score + valuation_score + timing_score

    if   total_score >= 80: final_action = "STRONG BUY"
    elif total_score >= 65: final_action = "BUY"
    elif total_score >= 50: final_action = "ACCUMULATE"
    elif total_score >= 35: final_action = "HOLD"
    elif total_score >= 20: final_action = "WATCH"
    elif total_score >= 10: final_action = "AVOID"
    else:                   final_action = "SELL"

    return (
        quality_score,
        valuation_score,
        timing_score,
        total_score,
        final_action,
        strengths[:4],
        weaknesses[:3],
    )

# ══════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════
def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — skipping alert")
        return False
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            log.info("Telegram alert sent")
            return True
        else:
            log.warning(f"Telegram failed: {resp.text}")
            return False
    except Exception as e:
        log.warning(f"Telegram error: {e}")
        return False

def build_alert_message(alerts, portfolio_value, top_results):
    now = datetime.now().strftime("%d-%b-%Y %H:%M")
    msg  = f"<b>SiddeGowda Portfolio Update</b>\n"
    msg += f"<i>{now} IST</i>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Portfolio: ₹{portfolio_value:,.0f}\n\n"

    if alerts["sl_breach"]:
        msg += "<b>🔴 STOP LOSS BREACHED</b>\n"
        for a in alerts["sl_breach"]:
            msg += f"  {a['sym']} — CMP ₹{a['cmp']} | SL ₹{a['sl']}\n"
        msg += "\n"

    if alerts["target_hit"]:
        msg += "<b>🎯 TARGET HIT</b>\n"
        for a in alerts["target_hit"]:
            msg += f"  {a['sym']} — CMP ₹{a['cmp']} | Target ₹{a['tgt']}\n"
        msg += "\n"

    if alerts["strong_buy"]:
        msg += "<b>✅ STRONG BUY / BUY</b>\n"
        for a in alerts["strong_buy"][:5]:
            msg += f"  {a['sym']} — Score:{a['score']} | {a['action']}\n"
        msg += "\n"

    if alerts["sell_watch"]:
        msg += "<b>⚠️ AVOID / SELL</b>\n"
        for a in alerts["sell_watch"][:5]:
            msg += f"  {a['sym']} — Score:{a['score']} | {a['action']}\n"
        msg += "\n"

    if top_results:
        msg += "<b>🏆 Top 3 Picks Today</b>\n"
        for r in top_results[:3]:
            msg += f"  {r['sym']} — {r['action']} (Score:{r['total']})\n"

    msg += "\n<i>via GitHub Actions + yfinance</i>"
    return msg

# ══════════════════════════════════════════════
# GOOGLE SHEETS AUTH
# ══════════════════════════════════════════════
def get_gspread_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON not set.")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json), scopes=SCOPES
    )
    return gspread.authorize(creds)

# ══════════════════════════════════════════════
# READ SYMBOLS
# ══════════════════════════════════════════════
def read_symbols(sh):
    skip = {"TOTAL","SYMBOL","SUM","SUBTOTAL","GRAND","NA","N/A",""}
    symbols = []
    try:
        pws  = sh.worksheet("Portfolio")
        rows = pws.get_all_values()[1:]
        for row in rows:
            sym = row[1].strip().upper() if len(row) > 1 else ""
            if (sym and sym not in symbols and sym not in skip
                    and len(sym) <= 15 and sym.replace("&","").isalnum()):
                symbols.append(sym)
        log.info(f"Portfolio tab col B: {len(symbols)} symbols")
    except Exception as e:
        log.warning(f"Could not read Portfolio: {e}")
    return symbols

# ══════════════════════════════════════════════
# READ TRADES
# ══════════════════════════════════════════════
def read_trades(sh):
    try:
        return sh.worksheet("Trade Log").get_all_values()[1:]
    except:
        return []

def get_avg_buy_and_qty(sym, trades):
    total_cost, total_qty = 0.0, 0.0
    for t in trades:
        if not t[0]: continue
        if t[0].strip().upper() != sym: continue
        try:
            typ   = t[2].strip().upper()
            qty   = float(t[3])
            price = float(t[4])
            if typ == "BUY":
                total_cost += qty * price
                total_qty  += qty
            elif typ == "SELL" and total_qty > 0:
                avg = total_cost / total_qty
                total_cost -= qty * avg
                total_qty  -= qty
        except:
            continue
    total_qty = max(total_qty, 0)
    avg_buy   = round(total_cost / total_qty, 2) if total_qty > 0 else None
    return avg_buy, total_qty

def compute_xirr(cashflows, dates):
    if len(cashflows) < 2: return None
    rate = 0.1
    for _ in range(100):
        t0 = dates[0].toordinal()
        try:
            fv  = sum(cf/(1+rate)**((d.toordinal()-t0)/365.25) for cf,d in zip(cashflows,dates))
            dfv = sum(-((d.toordinal()-t0)/365.25)*cf/(1+rate)**((d.toordinal()-t0)/365.25+1) for cf,d in zip(cashflows,dates))
        except (ZeroDivisionError, OverflowError):
            return None
        if abs(dfv) < 1e-10: break
        new_rate = rate - fv/dfv
        if abs(new_rate - rate) < 1e-7: return new_rate
        rate = new_rate
        if rate <= -1: rate = -0.999
    return None

def get_xirr(sym, trades, current_price):
    cashflows, dates, total_qty = [], [], 0
    for t in trades:
        if not t[0]: continue
        if t[0].strip().upper() != sym: continue
        try:
            raw = str(t[1]).strip()
            dt  = None
            for fmt in ["%d-%m-%Y","%Y-%m-%d","%d/%m/%Y","%d-%b-%Y"]:
                try: dt = datetime.strptime(raw, fmt).date(); break
                except: pass
            if not dt: continue
            typ   = t[2].strip().upper()
            qty   = float(t[3])
            price = float(t[4])
            if typ == "BUY":
                cashflows.append(-qty*price); dates.append(dt); total_qty += qty
            elif typ == "SELL":
                cashflows.append(qty*price);  dates.append(dt); total_qty -= qty
        except:
            continue
    if total_qty > 0 and current_price:
        cashflows.append(total_qty * current_price)
        dates.append(date.today())
    if len(cashflows) < 2: return None
    r = compute_xirr(cashflows, dates)
    return round(r*100, 2) if r else None

# ══════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════
def fetch_technicals(sym):
    try:
        df = yf.download(
            sym + ".NS", period="1y", interval="1d",
            progress=False, threads=False
        )
        if df is None or len(df) < 50:
            return {}

        close  = df["Close"].squeeze()
        volume = df["Volume"].squeeze()

        delta    = close.diff()
        gain     = delta.clip(lower=0)
        loss     = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs       = avg_gain / avg_loss
        rsi      = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        sma50  = round(float(close.rolling(50).mean().iloc[-1]), 2)
        sma200 = round(float(close.rolling(200).mean().iloc[-1]), 2) if len(close) >= 200 else None
        ema20  = round(float(close.ewm(span=20).mean().iloc[-1]), 2)
        cmp    = round(float(close.iloc[-1]), 2)

        vol_today = float(volume.iloc[-1])
        vol_avg20 = float(volume.rolling(20).mean().iloc[-1])
        vol_spike = round(vol_today / vol_avg20, 2) if vol_avg20 > 0 else 1.0

        if sma200:
            if   cmp > sma50 > sma200: trend = "Strong Uptrend"
            elif cmp > sma200:          trend = "Uptrend"
            elif cmp < sma50 < sma200: trend = "Strong Downtrend"
            elif cmp < sma200:          trend = "Downtrend"
            else:                       trend = "Sideways"
        else:
            trend = "Uptrend" if cmp > sma50 else "Downtrend"

        cross = ""
        if sma200 and len(close) >= 200:
            prev_sma50  = float(close.rolling(50).mean().iloc[-2])
            prev_sma200 = float(close.rolling(200).mean().iloc[-2])
            if sma50 > sma200 and prev_sma50 <= prev_sma200:
                cross = "Golden Cross"
            elif sma50 < sma200 and prev_sma50 >= prev_sma200:
                cross = "Death Cross"

        # Day change %
        prev_close = round(float(close.iloc[-2]), 2) if len(close) >= 2 else None
        day_chg_pct = round((cmp - prev_close) / prev_close * 100, 2) if prev_close else ""

        return {
            "rsi": rsi, "sma50": sma50,
            "sma200": sma200 or "", "ema20": ema20,
            "vol_spike": vol_spike, "trend": trend,
            "cross": cross, "cmp_tech": cmp,
            "day_chg_pct": day_chg_pct,
        }
    except Exception as e:
        log.warning(f"  technicals failed {sym}: {e}")
        return {}

# ══════════════════════════════════════════════
# FETCH FUNDAMENTALS
# ══════════════════════════════════════════════
def fetch_fundamentals(sym, retries=3):
    for attempt in range(retries):
        try:
            tk   = yf.Ticker(sym + ".NS")
            info = tk.info
            mcap_raw = info.get("marketCap", 0) or 0
            mcap_cr  = round(mcap_raw / 10_000_000, 0) if mcap_raw else None
            ebit = info.get("ebit", 0) or 0
            ta_  = info.get("totalAssets", 0) or 0
            tl   = info.get("totalCurrentLiabilities", 0) or 0
            roce = round(ebit / (ta_ - tl) * 100, 2) if (ta_ - tl) > 0 else None
            roa  = round(info.get("returnOnAssets", 0) * 100, 2) if info.get("returnOnAssets") else None
            return {
                "sector":   info.get("sector", ""),
                "industry": info.get("industry", ""),
                "high52":   info.get("fiftyTwoWeekHigh") or None,
                "low52":    info.get("fiftyTwoWeekLow")  or None,
                "mcap_cr":  mcap_cr,
                "pe":       round(info.get("trailingPE", 0), 2)         if info.get("trailingPE")     else None,
                "eps":      round(info.get("trailingEps", 0), 2)        if info.get("trailingEps")    else None,
                "bv":       round(info.get("bookValue", 0), 2)          if info.get("bookValue")      else None,
                "pb":       round(info.get("priceToBook", 0), 2)        if info.get("priceToBook")    else None,
                "div":      round(info.get("dividendYield", 0) * 100, 2)if info.get("dividendYield") else None,
                "roe":      round(info.get("returnOnEquity", 0) * 100, 2)if info.get("returnOnEquity")else None,
                "roa":      roa,
                "roce":     roce,
                "debt_eq":  round(info.get("debtToEquity", 0), 2)      if info.get("debtToEquity")  else None,
                "beta":     round(info.get("beta", 0), 2)               if info.get("beta")          else None,
            }
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                wait = (attempt + 1) * 15
                log.warning(f"  Rate limited {sym}, waiting {wait}s")
                time.sleep(wait)
            else:
                log.warning(f"  fundamentals failed {sym}: {e}")
                return {}
    return {}

def fetch_rev_growth(sym):
    try:
        fin = yf.Ticker(sym + ".NS").financials
        if fin is not None and not fin.empty and "Total Revenue" in fin.index:
            rv = fin.loc["Total Revenue"].dropna()
            if len(rv) >= 2:
                return round((rv.iloc[0] - rv.iloc[1]) / abs(rv.iloc[1]) * 100, 2)
    except:
        pass
    return None

# ══════════════════════════════════════════════
# BATCH PRICE FETCH
# ══════════════════════════════════════════════
def fetch_prices_batch(symbols):
    prices  = {}
    ns_syms = [s + ".NS" for s in symbols]
    for i in range(0, len(ns_syms), BATCH_SIZE):
        batch      = ns_syms[i:i + BATCH_SIZE]
        batch_orig = symbols[i:i + BATCH_SIZE]
        for attempt in range(3):
            try:
                df = yf.download(
                    tickers=batch, period="5d", interval="1d",
                    group_by="ticker", auto_adjust=True,
                    progress=False, threads=False
                )
                for sym, ns in zip(batch_orig, batch):
                    try:
                        close = df[ns]["Close"].dropna().iloc[-1] if len(batch) > 1 else df["Close"].dropna().iloc[-1]
                        prices[sym] = round(float(close), 2)
                    except:
                        prices[sym] = None
                log.info(f"Batch {i // BATCH_SIZE + 1}: {len(batch)} prices fetched")
                break
            except Exception as e:
                if "429" in str(e) or "Too Many" in str(e):
                    wait = (attempt + 1) * 20
                    log.warning(f"Batch rate limited, waiting {wait}s")
                    time.sleep(wait)
                else:
                    log.warning(f"Batch failed: {e}")
                    for sym in batch_orig: prices[sym] = None
                    break
        time.sleep(SLEEP_BATCH)
    return prices

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

def batch_update_safe(sh, requests, chunk=100):
    for i in range(0, len(requests), chunk):
        sh.batch_update({"requests": requests[i:i + chunk]})
        time.sleep(0.2)

# ══════════════════════════════════════════════
# ROW COLUMN MAP — mirrors the list built in build_result_row().
# Exported so telegram_bot / services read cached sheet rows
# without re-deriving indices.
# ══════════════════════════════════════════════
GITHUB_DATA_COLS = {
    "symbol": 0, "sector": 1, "industry": 2, "archetype": 3, "cmp": 4,
    "high52": 5, "low52": 6, "pct_high": 7,
    "pe": 8, "eps": 9, "bv": 10, "pb": 11,
    "div": 12, "roe": 13, "roa": 14, "debt_eq": 15,
    "rev_growth": 16, "beta": 17,
    "quality": 18, "valuation": 19, "timing": 20, "total": 21,
    "action": 22, "strengths": 23, "weaknesses": 24,
    "xirr": 25, "updated": 26,
    "rsi": 27, "sma50": 28, "sma200": 29, "ema20": 30, "vol_spike": 31, "trend": 32,
    "mcap": 33, "cap_type": 34,
}

# ══════════════════════════════════════════════
# BUILD A SINGLE RESULT ROW
# Shared by the Portfolio (GITHUB DATA) pipeline and any
# watchlist-only tab (e.g. Future Buy). Keeps row layout,
# scoring, and formatting identical everywhere.
# xirr_val is left as "" for watchlist symbols (no holdings).
# ══════════════════════════════════════════════
def build_result_row(sym, cmp, f, tech, rev_gr, xirr_val=""):
    sector   = f.get("sector", "")
    industry = f.get("industry", "")
    high52   = f.get("high52")
    low52    = f.get("low52")
    mcap_cr  = f.get("mcap_cr")

    archetype = get_archetype(sym, sector, industry)

    cap_type = ""
    if mcap_cr:
        if   mcap_cr >= 25000: cap_type = "Large Cap"
        elif mcap_cr >= 5000:  cap_type = "Mid Cap"
        else:                   cap_type = "Small Cap"

    pct_high         = round((cmp - high52) / high52 * 100, 2) if high52 else ""
    pct_high_display = f"{pct_high}%" if pct_high != "" else ""
    mcap_fmt         = indian_cr(mcap_cr) if mcap_cr else ""

    rsi         = tech.get("rsi", "")
    sma50       = tech.get("sma50", "")
    sma200      = tech.get("sma200", "")
    ema20       = tech.get("ema20", "")
    vol_spike   = tech.get("vol_spike", "")
    trend       = tech.get("trend", "")
    cross       = tech.get("cross", "")
    day_chg_pct = tech.get("day_chg_pct", "")

    metrics = {
        "roe":        f.get("roe"),
        "roa":        f.get("roa"),
        "roce":       f.get("roce"),
        "rev_growth": rev_gr,
        "debt_eq":    f.get("debt_eq"),
        "pe":         f.get("pe"),
        "pb":         f.get("pb"),
        "div":        f.get("div"),
        "rsi":        rsi if rsi != "" else None,
        "sma200":     sma200 if sma200 != "" else None,
        "cmp":        cmp,
        "vol_spike":  vol_spike if vol_spike != "" else None,
        "cross":      cross,
    }

    q_sc, v_sc, t_sc, tot_sc, final_action, strengths, weaknesses = compute_unified_score(
        sym, archetype, metrics
    )

    strengths_str  = " | ".join(strengths)
    weaknesses_str = " | ".join(weaknesses)

    row = [
        sym, sector, industry, archetype, cmp,
        high52 or "", low52 or "", day_chg_pct, pct_high_display,
        f.get("pe") or "", f.get("eps") or "", f.get("bv") or "", f.get("pb") or "",
        f.get("div") or "", f.get("roe") or "", f.get("roa") or "", f.get("debt_eq") or "",
        rev_gr or "", f.get("beta") or "",
        q_sc, v_sc, t_sc, tot_sc,
        final_action,
        strengths_str, weaknesses_str,
        xirr_val if xirr_val else "",
        datetime.now().strftime("%d-%b-%Y %H:%M"),
        rsi, sma50, sma200, ema20, vol_spike, trend,
        mcap_fmt, cap_type
    ]
    return row, archetype, tot_sc, final_action
def clean_row(row):
    return [("" if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v) for v in row]
# ══════════════════════════════════════════════
# WRITE A GITHUB-DATA-STYLE TAB
# Generic writer reused for "GITHUB DATA" and any
# watchlist tab (e.g. "Future Buy") — identical layout,
# sorting-ready columns, search/filter-friendly headers,
# styling, and coloring rules.
# ══════════════════════════════════════════════
def write_github_data(sh, rows, tab_name="GITHUB DATA"):
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except:
        ws = sh.add_worksheet(tab_name, rows=300, cols=35)

    headers = [
        "Symbol","Sector","Industry","Archetype","CMP",
        "52W High", "52W Low", "Day Chg%", "Buy 20% Less",
        "PE","EPS","Book Value","P/B",
        "Div Yield%","ROE%","ROA%","Debt/Equity",
        "Rev Growth%","Beta",
        "Quality Score","Valuation Score","Timing Score","Total Score",
        "Final Action",
        "Strengths","Weaknesses",
        "XIRR%","Updated",
        "RSI","SMA 50","SMA 200","EMA 20","Vol Spike","Trend",
        "Mkt Cap Cr","Cap Type"
    ]
    ws.append_row(headers)
    if rows:
        rows = [clean_row(r) for r in rows]
        ws.append_rows(rows)

    sh.batch_update({"requests": [{"repeatCell": {
        "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": len(headers)},
        "cell": {"userEnteredFormat": {
            "backgroundColor": hex_rgb("0d1b2a"),
            "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 8},
            "verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP"
        }},
        "fields": "userEnteredFormat"
    }}]})

    sh.batch_update({"requests": [{"updateSheetProperties": {
        "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1}},
        "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }}]})

    sh.batch_update({"requests": [{"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 50}, "fields": "pixelSize"
    }}]})

    widths = [
        70, 75, 90, 80, 55,
        55, 55, 55, 65,
        45, 45, 55, 45,
        50, 50, 50, 55,
        55, 45,
        55, 60, 55, 55,
        90,
        200, 200,
        55, 90,
        45, 55, 55, 55, 55, 90,
        65, 65
    ]
    sh.batch_update({"requests": [{"updateDimensionProperties": {
        "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
        "properties": {"pixelSize": w}, "fields": "pixelSize"
    }} for i, w in enumerate(widths)]})

    all_out = ws.get_all_values()[1:]
    reqs    = []

    ACTION_COLORS = {
        "STRONG BUY":  ("00c853", "ffffff"),
        "BUY":         ("0b8043", "ffffff"),
        "ACCUMULATE":  ("d9ead3", "0b8043"),
        "HOLD":        ("fff2cc", "7f4f00"),
        "WATCH":       ("fce8b2", "7f4f00"),
        "AVOID":       ("fde9d9", "c62828"),
        "SELL":        ("cc0000", "ffffff"),
    }

    for i, row in enumerate(all_out):
        rn  = i + 1
        alt = "f8f9fa" if i % 2 == 0 else "ffffff"
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn + 1,
                      "startColumnIndex": 0, "endColumnIndex": len(headers)},
            "cell": {"userEnteredFormat": {"backgroundColor": hex_rgb(alt)}},
            "fields": "userEnteredFormat.backgroundColor"
        }})

        def sf(idx):
            try:
                v = str(row[idx]).replace("%", "").replace(",", "").replace("₹", "").replace(" Cr", "").strip()
                return float(v) if len(row) > idx and v else None
            except:
                return None

        cap    = row[34].strip() if len(row) > 34 else ""
        action = row[22].strip() if len(row) > 22 else ""
        pct      = sf(8)   # Buy 20% Less shifted by 1
        day_chg  = sf(7)   # Day Chg% now at index 7
        rsi_v  = sf(27)
        q_sc   = sf(18)
        v_sc   = sf(19)
        t_sc   = sf(20)
        tot_sc = sf(21)

        if cap == "Large Cap":       cb, cf = "d9ead3", "0b8043"
        elif cap == "Mid Cap":       cb, cf = "d9eaf7", "1565c0"
        elif cap == "Small Cap":     cb, cf = "fde9d9", "c62828"
        else:                        cb, cf = "ffffff", "000000"
        if cap:
            reqs += [
                color_cell_req(ws.id, rn, 0, cb, cf),
                color_cell_req(ws.id, rn, 33, cb, cf),
                color_cell_req(ws.id, rn, 34, cb, cf),
            ]

        reqs.append(color_cell_req(ws.id, rn, 5, "eaf4fb", "1565c0", bold=False))
        reqs.append(color_cell_req(ws.id, rn, 6, "fdf2f2", "c62828", bold=False))

        # Day Chg% — solid green/red background matching Portfolio tab exactly
        if day_chg is not None:
            if day_chg > 0:
                reqs.append({
                    "repeatCell": {
                        "range": {"sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn+1,
                                  "startColumnIndex": 7, "endColumnIndex": 8},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": hex_rgb("d9ead3"),
                            "textFormat": {"foregroundColor": hex_rgb("0b8043"), "bold": True}
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)"
                    }
                })
            elif day_chg < 0:
                reqs.append({
                    "repeatCell": {
                        "range": {"sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn+1,
                                  "startColumnIndex": 7, "endColumnIndex": 8},
                        "cell": {"userEnteredFormat": {
                            "backgroundColor": hex_rgb("fde9d9"),
                            "textFormat": {"foregroundColor": hex_rgb("c62828"), "bold": True}
                        }},
                        "fields": "userEnteredFormat(backgroundColor,textFormat)"
                    }
                })

        if pct is not None:
            reqs.append(color_cell_req(ws.id, rn, 8, "d9ead3", "0b8043") if pct >= -20
                        else color_cell_req(ws.id, rn, 8, "fde9d9", "c62828"))

        if action in ACTION_COLORS:
            bg_a, fg_a = ACTION_COLORS[action]
            reqs.append(color_cell_req(ws.id, rn, 22, bg_a, fg_a))

        if q_sc is not None:
            if q_sc >= 30:   reqs.append(color_cell_req(ws.id, rn, 18, "d9ead3", "0b8043"))
            elif q_sc <= 15: reqs.append(color_cell_req(ws.id, rn, 18, "fde9d9", "c62828"))

        if v_sc is not None:
            if v_sc >= 22:   reqs.append(color_cell_req(ws.id, rn, 19, "d9ead3", "0b8043"))
            elif v_sc <= 10: reqs.append(color_cell_req(ws.id, rn, 19, "fde9d9", "c62828"))

        if t_sc is not None:
            if t_sc >= 22:   reqs.append(color_cell_req(ws.id, rn, 20, "d9ead3", "0b8043"))
            elif t_sc <= 10: reqs.append(color_cell_req(ws.id, rn, 20, "fde9d9", "c62828"))

        if tot_sc is not None:
            if   tot_sc >= 65: reqs.append(color_cell_req(ws.id, rn, 21, "00c853", "ffffff"))
            elif tot_sc >= 50: reqs.append(color_cell_req(ws.id, rn, 21, "d9ead3", "0b8043"))
            elif tot_sc >= 35: reqs.append(color_cell_req(ws.id, rn, 21, "fff2cc", "7f4f00"))
            else:              reqs.append(color_cell_req(ws.id, rn, 21, "fde9d9", "c62828"))

        if rsi_v is not None:
            if   rsi_v < 35:  reqs.append(color_cell_req(ws.id, rn, 27, "d9ead3", "0b8043"))
            elif rsi_v > 70:  reqs.append(color_cell_req(ws.id, rn, 27, "fde9d9", "c62828"))
            elif rsi_v > 60:  reqs.append(color_cell_req(ws.id, rn, 27, "fff2cc", "7f4f00"))

    batch_update_safe(sh, reqs)
    log.info(f"{tab_name} tab written and formatted")
    return ws

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
    growth = []

    for row in all_out:
        if not row or not row[0]: continue
        sym    = row[0].strip()
        action = row[22].strip() if len(row) > 22 else ""
        cap    = row[34].strip() if len(row) > 34 else ""

        def sf(v):
            try: return float(str(v).replace("%", "").replace(",", "").replace("₹", "").replace(" Cr", "").strip())
            except: return None

        tot_sc = sf(row[21] if len(row) > 21 else "")
        q_sc   = sf(row[18] if len(row) > 18 else "")
        v_sc   = sf(row[19] if len(row) > 19 else "")
        t_sc   = sf(row[20] if len(row) > 20 else "")
        rsi    = row[27] if len(row) > 27 else ""
        trend  = row[32] if len(row) > 32 else ""

        growth.append([
            sym, cap,
            row[8]  if len(row) > 8  else "",
            row[13] if len(row) > 13 else "",
            row[15] if len(row) > 15 else "",
            row[16] if len(row) > 16 else "",
            row[12] if len(row) > 12 else "",
            row[7]  if len(row) > 7  else "",
            q_sc or "", v_sc or "", t_sc or "", tot_sc or "",
            action,
            row[23] if len(row) > 23 else "",
            row[24] if len(row) > 24 else "",
            rsi, trend,
        ])

    growth.sort(key=lambda x: float(x[11]) if x[11] != "" else 0, reverse=True)

    try:
        gsw = sh.worksheet("Growth Screener")
        gsw.clear()
    except:
        gsw = sh.add_worksheet("Growth Screener", rows=200, cols=18)

    gsw.append_row([
        "Symbol", "Cap Type",
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

# ══════════════════════════════════════════════
# WATCHLIST PROCESSING (Future Buy, and any future
# watchlist added to WATCHLISTS). Market-data only —
# no qty, no buy price, no purchase date, no XIRR/SL/
# target alerts. Duplicate symbols vs Portfolio/GITHUB
# DATA are independent here and never touch holdings
# or portfolio-value calculations.
# ══════════════════════════════════════════════
def process_watchlist_tab(sh, tab_name, symbols):
    if not symbols:
        log.warning(f"{tab_name}: no symbols configured, skipping")
        return []

    log.info(f"{tab_name}: fetching prices for {len(symbols)} symbols...")
    prices = fetch_prices_batch(symbols)

    rows = []
    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            log.warning(f"  SKIP {sym} ({tab_name}) — no price")
            continue

        f      = fetch_fundamentals(sym)
        rev_gr = fetch_rev_growth(sym)
        tech   = fetch_technicals(sym)
        time.sleep(SLEEP_INFO)

        row, archetype, tot_sc, final_action = build_result_row(sym, cmp, f, tech, rev_gr, xirr_val="")
        rows.append(row)
        log.info(f"  {sym:12} | {archetype:25} | Total:{tot_sc:3} | {final_action}")

    write_github_data(sh, rows, tab_name=tab_name)
    return rows

def process_all_watchlists(sh):
    for tab_name, symbols in WATCHLISTS.items():
        try:
            process_watchlist_tab(sh, tab_name, symbols)
        except Exception as e:
            log.error(f"Watchlist '{tab_name}' failed (existing tabs unaffected): {e}")

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def run_portfolio_update(sh):
    """
    Runs the full daily pipeline: fetch prices/fundamentals/technicals,
    score every symbol, write GITHUB DATA + Growth Screener + all
    WATCHLISTS tabs. Returns everything the caller needs (GitHub Actions
    cron AND the /refresh bot command both call this — single source
    of truth, no duplicated pipeline logic).
    """
    symbols = read_symbols(sh)
    if not symbols:
        log.error("No symbols found.")
        return None

    trades = read_trades(sh)
    log.info(f"Found {len(symbols)} symbols")

    log.info("Fetching prices...")
    prices = fetch_prices_batch(symbols)

    log.info("Fetching fundamentals + technicals...")
    fund_map, tech_map, rev_map = {}, {}, {}
    for sym in symbols:
        f = fetch_fundamentals(sym)
        fund_map[sym] = f
        rev_map[sym]  = fetch_rev_growth(sym)
        log.info(f"  Technicals: {sym}")
        tech_map[sym] = fetch_technicals(sym)
        time.sleep(SLEEP_INFO)

    holdings, portfolio_live_value = {}, 0.0
    for sym in symbols:
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        cmp = prices.get(sym)
        if qty > 0 and cmp and cmp > 0:
            holdings[sym] = (qty, cmp, avg_buy)
            portfolio_live_value += qty * cmp

    results, failed = [], []
    alerts = {"sl_breach": [], "target_hit": [], "strong_buy": [], "sell_watch": []}
    top_picks = []

    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            failed.append(sym)
            log.warning(f"  SKIP {sym} — no price")
            continue

        f, tech, rev_gr = fund_map.get(sym, {}), tech_map.get(sym, {}), rev_map.get(sym)
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        xirr_val = get_xirr(sym, trades, cmp)

        row, archetype, tot_sc, final_action = build_result_row(sym, cmp, f, tech, rev_gr, xirr_val=xirr_val)

        if avg_buy and qty > 0:
            sl_price, tgt_price = avg_buy * (1 - SL_PCT), avg_buy * (1 + TARGET_PCT)
            if cmp <= sl_price:
                alerts["sl_breach"].append({"sym": sym, "cmp": cmp, "sl": round(sl_price, 2)})
            if cmp >= tgt_price:
                alerts["target_hit"].append({"sym": sym, "cmp": cmp, "tgt": round(tgt_price, 2)})

        if final_action in ("STRONG BUY", "BUY"):
            alerts["strong_buy"].append({"sym": sym, "score": tot_sc, "action": final_action})
            top_picks.append({"sym": sym, "total": tot_sc, "action": final_action})
        elif final_action in ("AVOID", "SELL"):
            alerts["sell_watch"].append({"sym": sym, "score": tot_sc, "action": final_action})

        results.append(row)
        log.info(f"  {sym:12} | {archetype:25} | Total:{tot_sc:3} | {final_action}")

    top_picks.sort(key=lambda x: x["total"], reverse=True)

    ws = write_github_data(sh, results, tab_name="GITHUB DATA")
    all_out = ws.get_all_values()[1:]
    write_growth_screener(sh, all_out)
    process_all_watchlists(sh)

    return {
        "results": results, "alerts": alerts,
        "portfolio_live_value": portfolio_live_value,
        "top_picks": top_picks, "failed": failed,
    }


def main():
    log.info("═" * 55)
    log.info("SIDDEGOWDA PORTFOLIO — Daily Auto-Update v2.0")
    log.info(f"Run time: {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}")
    log.info("═" * 55)

    gc = get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    log.info("Connected to Google Sheets")

    out = run_portfolio_update(sh)
    if out is None:
        send_telegram("❌ Portfolio update FAILED — no symbols found in Portfolio tab col B")
        return

    msg = build_alert_message(out["alerts"], out["portfolio_live_value"], out["top_picks"])
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n<i>...truncated</i>"
    send_telegram(msg)

    log.info("═" * 55)
    log.info(f"✅ {len(out['results'])} stocks updated | ❌ Failed: {out['failed'] or 'None'}")
    log.info(f"💰 Portfolio: ₹{out['portfolio_live_value']:,.0f}")
    log.info(f"🔴 SL Breach: {[a['sym'] for a in out['alerts']['sl_breach']] or 'None'}")
    log.info(f"🎯 Target Hit: {[a['sym'] for a in out['alerts']['target_hit']] or 'None'}")
    log.info(f"✅ Strong Buy: {[a['sym'] for a in out['alerts']['strong_buy'][:5]]}")
    log.info("Top 5 picks:")
    for r in out["top_picks"][:5]:
        log.info(f"   {r['sym']:<12} Score:{r['total']:>3}  {r['action']}")
    log.info("═" * 55)


if __name__ == "__main__":
    main()
