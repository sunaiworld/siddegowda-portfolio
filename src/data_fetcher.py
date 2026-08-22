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
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════
def fetch_technicals(sym):
    try:
        profiler.increment("Yahoo requests")
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
            profiler.increment("Yahoo requests")
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
                "shortName": info.get("shortName", ""),
                "sector":   info.get("sector", ""),
                "industry": info.get("industry", ""),
                "high52":   info.get("fiftyTwoWeekHigh") or None,
                "low52":    info.get("fiftyTwoWeekLow")  or None,
                "mcap_cr":  mcap_cr,
                "pe":       round(info.get("trailingPE", 0), 2)         if info.get("trailingPE")     else None,
                "eps":      round(info.get("trailingEps", 0), 2)        if info.get("trailingEps")    else None,
                "bv":       round(info.get("bookValue", 0), 2)          if info.get("bookValue")      else None,
                "pb":       round(info.get("priceToBook", 0), 2)        if info.get("priceToBook")    else None,
                "div":      round(info.get("dividendYield", 0), 6) if info.get("dividendYield") else None,
                "roe":      round(info.get("returnOnEquity", 0) * 100, 2)if info.get("returnOnEquity")else None,
                "roa":      roa,
                "roce":     roce,
                "debt_eq":  round(info.get("debtToEquity", 0), 2)      if info.get("debtToEquity")  else None,
                "beta":     round(info.get("beta", 0), 2)               if info.get("beta")          else None,
                "payout_ratio": round(info.get("payoutRatio", 0) * 100, 2) if info.get("payoutRatio") else None,
                "div_yield_5y": round(info.get("fiveYearAvgDividendYield", 0), 2) if info.get("fiveYearAvgDividendYield") else None,
                "div_rate": info.get("dividendRate") or None,
                "fcf": info.get("freeCashflow") or None,
                "ocf": info.get("operatingCashflow") or None,
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
        profiler.increment("Yahoo requests")
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
        batch_str  = " ".join(batch)
        for attempt in range(3):
            try:
                profiler.increment("Yahoo requests")
                df = yf.download(
                    tickers=batch_str, period="5d", interval="1d",
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

