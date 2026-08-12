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

# ══════════════════════════════════════════════
# ARCHETYPE RISK PROFILE MAP
# (Economic Sensitivity, Investor Role)
# ══════════════════════════════════════════════
ARCHETYPE_RISK_MAP = {
    "COMMODITY_CYCLICAL": ("Very High", "Cyclical"),
    "INDUSTRIAL_CAPEX": ("High", "Cyclical"),
    "CONSUMER_DISCRETIONARY": ("Medium-High", "Growth/Cyclical"),
    "CONSUMER_STAPLES": ("Low", "Defensive"),
    "FINANCIAL_BANK": ("Medium", "Financial"),
    "FINANCIAL_NBFC": ("Medium-High", "Financial/Growth"),
    "FINANCIAL_CAPITAL_MARKETS": ("High", "Market-sensitive"),
    "FINANCIAL_INSURANCE": ("Low-Medium", "Defensive"),
    "QUALITY_GROWTH": ("Medium", "Long-term Growth"),
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

def get_archetype_risk_profile(archetype):
    """
    Returns (Economic Sensitivity, Investor Role) 
    based on the assigned archetype.
    """
    return ARCHETYPE_RISK_MAP.get(archetype, ("", ""))


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

    # ── News Sentiment Modifier (bounded ±5 pts on Timing only) ──────────
    # Reads classifier output already stored in news_data. Never overrides
    # fundamentals — Quality and Valuation scores are unchanged. The cap of
    # ±5 means news alone can never flip a BUY to SELL or vice versa;
    # it acts as a tie-breaker when scores are close, and surfaces
    # news-driven momentum in the Strengths/Weaknesses audit trail.
    news_sentiment  = metrics.get("news_sentiment", "")
    news_bull       = metrics.get("news_bullish_score")
    news_bear       = metrics.get("news_bearish_score")
    if news_sentiment == "Bullish" and news_bull is not None:
        # Scale: bullish_score 1..10 → +1..+5 pts
        adj = min(5, max(1, round(news_bull / 2)))
        timing_score += adj
        strengths.append(f"✓ Bullish news signal ({int(news_bull)}/10) — positive momentum")
    elif news_sentiment == "Bearish" and news_bear is not None:
        adj = min(5, max(1, round(news_bear / 2)))
        timing_score = max(0, timing_score - adj)
        weaknesses.append(f"✗ Bearish news signal ({int(news_bear)}/10) — negative momentum")

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
# TECHNICAL SETUP CLASSIFICATION
# Informational only — reuses fields already returned by
# fetch_technicals(). Does not read metrics{}, does not touch
# compute_unified_score(), Total Score, or Final Action anywhere.
# No new fetch, no new API call.
# ══════════════════════════════════════════════
def classify_technical_setup(tech, cmp):
    sma50  = tech.get("sma50")
    sma200 = tech.get("sma200")
    ema20  = tech.get("ema20")
    rsi    = tech.get("rsi")
    vol_spike   = tech.get("vol_spike")
    trend       = tech.get("trend", "")
    day_chg_pct = tech.get("day_chg_pct")

    if not sma50 or not ema20 or rsi is None or not cmp:
        return "⚪ Unknown"

    dist_ema20 = (cmp - ema20) / ema20 * 100 if ema20 else 0
    dist_sma50 = (cmp - sma50) / sma50 * 100 if sma50 else 0
    above_sma200 = (sma200 is not None and sma200 != "" and cmp > sma200)

    day_chg_val = day_chg_pct if isinstance(day_chg_pct, (int, float)) else None
    vol_val     = vol_spike if isinstance(vol_spike, (int, float)) else None

    # Priority 2: Breakout — checked before the generic Volatile
    # trigger so a genuine high-volume breakout isn't swallowed by
    # the broader volatility check that shares the same vol_spike
    # threshold.
    if (trend == "Strong Uptrend" and vol_val is not None and vol_val >= 2.0
            and rsi is not None and 55 <= rsi <= 70 and cmp > sma50):
        return "🟣 Breakout"

    if (vol_val is not None and vol_val >= 2.0) or (day_chg_val is not None and abs(day_chg_val) >= 4):
        return "🟠 Volatile"

    if trend in ("Strong Uptrend", "Uptrend") and rsi is not None and rsi >= 70 and dist_ema20 >= 8:
        return "🔴 Extended"

    if above_sma200 and cmp < sma50 and rsi is not None and 35 <= rsi <= 55:
        return "🔵 Pullback"

    if (abs(dist_ema20) <= 3 and abs(dist_sma50) <= 3
            and rsi is not None and 45 <= rsi <= 55
            and vol_val is not None and vol_val < 1.5):
        return "🟢 Tight Base"

    if trend == "Sideways" or (abs(dist_sma50) <= 6 and rsi is not None and 40 <= rsi <= 60):
        return "🟡 Consolidating"

    return "🟡 Consolidating"

def score_symbol(sym, cmp, f, tech, rev_gr, news_data=None):
    sector   = f.get("sector", "")
    industry = f.get("industry", "")
    archetype = get_archetype(sym, sector, industry)

    rsi         = tech.get("rsi", "")
    sma200      = tech.get("sma200", "")
    vol_spike   = tech.get("vol_spike", "")
    trend       = tech.get("trend", "")
    cross       = tech.get("cross", "")

    _nd = news_data or {}
    try:
        _bull = float(_nd.get("bullish_score", "")) if _nd.get("bullish_score", "") != "" else None
    except (TypeError, ValueError):
        _bull = None
    try:
        _bear = float(_nd.get("bearish_score", "")) if _nd.get("bearish_score", "") != "" else None
    except (TypeError, ValueError):
        _bear = None

    metrics = {
        "roe": f.get("roe"), "roa": f.get("roa"), "roce": f.get("roce"),
        "rev_growth": rev_gr, "debt_eq": f.get("debt_eq"),
        "pe": f.get("pe"), "pb": f.get("pb"), "div": f.get("div"),
        "rsi": rsi if rsi != "" else None,
        "sma200": sma200 if sma200 != "" else None,
        "cmp": cmp,
        "vol_spike": vol_spike if vol_spike != "" else None,
        "cross": cross,
        "news_sentiment": _nd.get("sentiment", ""),
        "news_bullish_score": _bull,
        "news_bearish_score": _bear,
    }

    q_sc, v_sc, t_sc, tot_sc, final_action, strengths, weaknesses = compute_unified_score(
        sym, archetype, metrics
    )

    return {
        "archetype": archetype, "quality": q_sc, "valuation": v_sc,
        "timing": t_sc, "total": tot_sc, "final_action": final_action,
        "strengths": strengths, "weaknesses": weaknesses,
    }

# ══════════════════════════════════════════════
# BUYING ZONE CLASSIFICATION
# ══════════════════════════════════════════════
def calculate_buying_zone(quality_score, valuation_score, total_score=None, metrics=None):
    """
    Evaluates whether a stock is a buy at current valuation/fundamental scores.
    Outputs: ❌ WAIT, 🟡 SMALL BUY, 🟢 ACCUMULATE, 🟢🟢 ADD AGGRESSIVELY, 🔎 INVESTIGATE WHY
    """
    if quality_score is None:
        quality_score = 0
    if valuation_score is None:
        valuation_score = 0
    
    _m = metrics or {}
    pe = _m.get("pe")
    debt_eq = _m.get("debt_eq")
    rev_gr = _m.get("rev_growth")
    
    # 1. INVESTIGATE WHY: Very large valuation discount but significant uncertainty/warning
    # Extremely cheap valuation (v_sc >= 25 or very low PE) but poor quality or shrinking revenue
    if (valuation_score >= 25 or (pe is not None and pe < 10 and pe > 0)) and (quality_score < 15 or (rev_gr is not None and rev_gr < 0)):
        return "🔎 INVESTIGATE WHY"
        
    # 2. ADD AGGRESSIVELY: Price is significantly below attractive valuation AND fundamentals are strong
    # Must have high quality, very cheap valuation, manageable debt, and positive growth
    if quality_score >= 25 and valuation_score >= 22:
        if (debt_eq is None or debt_eq < 1.5) and (rev_gr is None or rev_gr > 0):
            return "🟢🟢 ADD AGGRESSIVELY"
        
    # 3. ACCUMULATE: Price is attractive enough to start building a position
    # High quality, reasonable valuation, OR exceptional quality with slightly premium valuation
    if (quality_score >= 25 and valuation_score >= 15) or (quality_score >= 32 and valuation_score >= 10):
        return "🟢 ACCUMULATE"
        
    # 4. SMALL BUY: Price is becoming reasonable but is not yet strongly attractive
    # Medium quality with decent valuation, or high quality but slightly expensive
    if (quality_score >= 15 and valuation_score >= 15) or (quality_score >= 25 and valuation_score >= 5):
        return "🟡 SMALL BUY"
        
    # 5. WAIT: Current price is not attractive enough (Expensive valuation or poor fundamentals)
    return "❌ WAIT"


