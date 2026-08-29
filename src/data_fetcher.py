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
# ══════════════════════════════════════════════
# NIFTY 50 BENCHMARK CACHE & BETA CALCULATION
# ══════════════════════════════════════════════
_NIFTY_HISTORY = None

def get_nifty_history():
    """Fetch and cache 1Y daily close history for NIFTY 50 (^NSEI)."""
    global _NIFTY_HISTORY
    if _NIFTY_HISTORY is not None and len(_NIFTY_HISTORY) >= 50:
        return _NIFTY_HISTORY
    try:
        profiler.increment("Yahoo requests")
        df = yf.download("^NSEI", period="1y", interval="1d", progress=False, threads=False)
        if df is not None and not df.empty and "Close" in df:
            _NIFTY_HISTORY = df["Close"].squeeze()
    except Exception as e:
        log.warning(f"Failed to fetch NIFTY 50 index data for beta calculation: {e}")
    return _NIFTY_HISTORY

def compute_nifty_beta(stock_closes, nifty_closes):
    """
    Computes domestic Beta against NIFTY 50 using daily returns:
    Beta = Cov(R_stock, R_nifty) / Var(R_nifty)
    """
    try:
        if stock_closes is None or nifty_closes is None:
            return None
        s_ret = stock_closes.pct_change().dropna()
        n_ret = nifty_closes.pct_change().dropna()
        common = s_ret.index.intersection(n_ret.index)
        if len(common) < 30:
            return None
        s = s_ret.loc[common]
        n = n_ret.loc[common]
        cov = np.cov(s, n)[0][1]
        var_n = np.var(n, ddof=1)
        if var_n > 0:
            return round(float(cov / var_n), 2)
    except Exception as e:
        log.debug(f"compute_nifty_beta failed: {e}")
    return None

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

        # Day change % (numeric float)
        prev_close = round(float(close.iloc[-2]), 2) if len(close) >= 2 else None
        day_chg_pct = round((cmp - prev_close) / prev_close * 100, 2) if prev_close else ""

        # 1W Return % (numeric float, approx 5 trading sessions ago)
        return_1w = ""
        if len(close) >= 6:
            p5 = float(close.iloc[-6])
            if p5 > 0:
                return_1w = round((cmp / p5 - 1) * 100, 2)

        # 1M Return % (numeric float, approx 21 trading sessions ago)
        return_1m = ""
        if len(close) >= 22:
            p21 = float(close.iloc[-22])
            if p21 > 0:
                return_1m = round((cmp / p21 - 1) * 100, 2)

        # Domestic Beta vs NIFTY 50
        nifty_history = get_nifty_history()
        beta_nifty = compute_nifty_beta(close, nifty_history) if nifty_history is not None else None

        return {
            "rsi": rsi, "sma50": sma50,
            "sma200": sma200 or "", "ema20": ema20,
            "vol_spike": vol_spike, "trend": trend,
            "cross": cross, "cmp_tech": cmp,
            "day_chg_pct": day_chg_pct,
            "return_1w": return_1w,
            "return_1m": return_1m,
            "beta_nifty": beta_nifty,
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

            # ── Dividend yield — computed directly, not trusted from yfinance ──
            # yfinance's info["dividendYield"] has flip-flopped between fraction
            # (0.0403) and percent (4.03) scale across library/Yahoo-backend
            # versions, and isn't guaranteed consistent across all tickers even
            # within one version. Trusting it silently produces wrong yields
            # whenever that scale shifts again. dividendRate (absolute Rs/share)
            # has no such ambiguity, so we derive yield ourselves from it and
            # the reference price — immune to any future change on Yahoo's side.
            div_rate_raw = info.get("dividendRate")
            ref_price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
            )
            div_yield_pct = (
                round(div_rate_raw / ref_price * 100, 2)
                if (div_rate_raw and ref_price)
                else None
            )

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
                "div":      div_yield_pct,
                "roe":      round(info.get("returnOnEquity", 0) * 100, 2)if info.get("returnOnEquity")else None,
                "roa":      roa,
                "roce":     roce,
                "debt_eq":  round(info.get("debtToEquity", 0), 2)      if info.get("debtToEquity")  else None,
                "beta":     round(info.get("beta", 0), 2)               if info.get("beta")          else None,
                "payout_ratio": round(info.get("payoutRatio", 0) * 100, 2) if info.get("payoutRatio") else None,
                "div_yield_5y": round(info.get("fiveYearAvgDividendYield", 0), 2) if info.get("fiveYearAvgDividendYield") else None,
                "div_rate": div_rate_raw or None,
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
    except Exception as e:
        log.debug(f"fetch_rev_growth failed for {sym}: {e}")
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
