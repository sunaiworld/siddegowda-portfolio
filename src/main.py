#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Daily Auto-Updater
Long-term + Swing Trading Dashboard
GitHub Actions — runs daily 6 PM IST
"""

import os
import json
import time
import logging
import statistics
import requests
from datetime import datetime, date

import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# ── ta library for technical indicators ───────
try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    TA_AVAILABLE = False

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
BATCH_SIZE       = 5
SLEEP_BATCH      = 8
SLEEP_INFO       = 3

# ══════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════
def send_telegram(message):
    """Send message to Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — skipping alert")
        return False
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "HTML"
        }
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

def build_alert_message(alerts, portfolio_value, top_growth):
    """Build formatted Telegram message."""
    now = datetime.now().strftime("%d-%b-%Y %H:%M")
    msg = f"<b>SiddeGowda Portfolio Update</b>\n"
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

    if alerts["sell_signal"]:
        msg += "<b>⚠️ SELL SIGNALS</b>\n"
        for a in alerts["sell_signal"]:
            msg += f"  {a['sym']} — {a['reason']}\n"
        msg += "\n"

    if alerts["swing_buy"]:
        msg += "<b>📈 SWING BUY OPPORTUNITIES</b>\n"
        for a in alerts["swing_buy"]:
            msg += f"  {a['sym']} — RSI:{a['rsi']} | {a['reason']}\n"
        msg += "\n"

    if alerts["strong_buy"]:
        msg += "<b>✅ STRONG BUY (Growth)</b>\n"
        for a in alerts["strong_buy"][:3]:
            msg += f"  {a['sym']} — Score:{a['score']} | {a['reason']}\n"
        msg += "\n"

    if top_growth:
        msg += "<b>🏆 Top 3 Growth Picks</b>\n"
        for r in top_growth[:3]:
            msg += f"  {r[0]} — {r[12]} (Score:{r[11]})\n"

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
# TECHNICAL INDICATORS — RSI + Moving Averages
# ══════════════════════════════════════════════
def fetch_technicals(sym):
    """
    Fetch 1 year of daily data and calculate:
    - RSI (14 day)
    - SMA 50 day
    - SMA 200 day
    - EMA 20 day
    - Volume spike (today vs 20 day avg)
    - Trend (bullish/bearish/neutral)
    - Swing signal
    """
    try:
        df = yf.download(
            sym + ".NS",
            period   = "1y",
            interval = "1d",
            progress = False,
            threads  = False
        )

        if df is None or len(df) < 50:
            return {}

        close  = df["Close"].squeeze()
        volume = df["Volume"].squeeze()

        # ── RSI (14 period) ────────────────────
        delta = close.diff()
        gain  = delta.clip(lower=0)
        loss  = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs  = avg_gain / avg_loss
        rsi = round(float(100 - (100 / (1 + rs.iloc[-1]))), 1)

        # ── Moving Averages ───────────────────
        sma50  = round(float(close.rolling(50).mean().iloc[-1]),  2)
        sma200 = round(float(close.rolling(200).mean().iloc[-1]), 2) if len(close) >= 200 else None
        ema20  = round(float(close.ewm(span=20).mean().iloc[-1]), 2)
        cmp    = round(float(close.iloc[-1]), 2)

        # ── Volume spike ──────────────────────
        vol_today  = float(volume.iloc[-1])
        vol_avg20  = float(volume.rolling(20).mean().iloc[-1])
        vol_spike  = round(vol_today / vol_avg20, 2) if vol_avg20 > 0 else 1.0

        # ── Trend detection ───────────────────
        if sma200:
            if cmp > sma50 > sma200:
                trend = "Strong Uptrend"
            elif cmp > sma200:
                trend = "Uptrend"
            elif cmp < sma50 < sma200:
                trend = "Strong Downtrend"
            elif cmp < sma200:
                trend = "Downtrend"
            else:
                trend = "Sideways"
        else:
            trend = "Uptrend" if cmp > sma50 else "Downtrend"

        # ── Golden / Death cross ──────────────
        cross = ""
        if sma200:
            prev_sma50  = float(close.rolling(50).mean().iloc[-2])
            prev_sma200 = float(close.rolling(200).mean().iloc[-2]) if len(close) >= 200 else None
            if prev_sma200:
                if sma50 > sma200 and prev_sma50 <= prev_sma200:
                    cross = "Golden Cross"
                elif sma50 < sma200 and prev_sma50 >= prev_sma200:
                    cross = "Death Cross"

        # ── Swing signal ──────────────────────
        swing_signal = ""
        swing_reason = ""

        # Swing BUY conditions
        if rsi < 35 and cmp > sma200 * 0.95 if sma200 else True:
            swing_signal = "SWING BUY"
            swing_reason = f"Oversold RSI {rsi}"
        elif rsi < 40 and cmp > sma50 and vol_spike > 1.5:
            swing_signal = "SWING BUY"
            swing_reason = f"RSI {rsi} + Volume spike {vol_spike}x"
        elif cross == "Golden Cross":
            swing_signal = "SWING BUY"
            swing_reason = "Golden Cross detected"

        # Swing SELL conditions
        elif rsi > 75:
            swing_signal = "SWING SELL"
            swing_reason = f"Overbought RSI {rsi}"
        elif rsi > 70 and cmp < sma20 if 'sma20' in dir() else False:
            swing_signal = "SWING SELL"
            swing_reason = f"RSI {rsi} + Below EMA20"
        elif cross == "Death Cross":
            swing_signal = "SWING SELL"
            swing_reason = "Death Cross detected"

        # Neutral
        elif 40 <= rsi <= 60:
            swing_signal = "NEUTRAL"
            swing_reason = f"RSI {rsi} — wait for setup"
        else:
            swing_signal = "WATCH"
            swing_reason = f"RSI {rsi}"

        return {
            "rsi":          rsi,
            "sma50":        sma50,
            "sma200":       sma200 or "",
            "ema20":        ema20,
            "vol_spike":    vol_spike,
            "trend":        trend,
            "cross":        cross,
            "swing_signal": swing_signal,
            "swing_reason": swing_reason,
        }

    except Exception as e:
        log.warning(f"  technicals failed {sym}: {e}")
        return {}

# ══════════════════════════════════════════════
# SECTOR CLASSIFICATION
# ══════════════════════════════════════════════
FINANCIAL_SECTORS = {
    "banks","bank","nbfc","insurance","financial services",
    "capital markets","diversified financials",
    "thrifts & mortgage finance","consumer finance",
    "asset management","mortgage finance"
}
FINANCIAL_KEYWORDS = {
    "BANK","FIN","FINCORP","FINSERV","CAPITAL","INSURANCE",
    "HOUSING","NBFC","AMC","INVEST","WEALTH","MONEY","CREDIT",
    "LENDING","MICRO","GOLD","MUTHOOT","MANAPPURAM","BAJAJFINSV",
    "BAJFINANCE","CHOLAFIN","SBICARD","SBILIFE","HDFCLIFE",
    "HDFCAMC","STARHEALTH","JIOFIN","NUVAMA","MOTILALOFS",
    "SHRIRAMFIN","SUNDARMFIN","TATAINVEST","JMFINANCIL","ARMANFIN",
    "FIVESTAR","AAVAS","APTUS","PNBHOUSING","UJJIVANSFB",
    "EQUITASBNK","BANDHANBNK","IDFCFIRSTB","CUB","KTKBANK",
    "GEOJITFSL","5PAISA","ANGELONE","CHOLAHLDNG"
}

def is_financial_stock(sym, sector="", industry=""):
    sym_upper      = sym.upper()
    sector_lower   = sector.lower()
    industry_lower = industry.lower()
    for fs in FINANCIAL_SECTORS:
        if fs in sector_lower or fs in industry_lower:
            return True
    for kw in FINANCIAL_KEYWORDS:
        if kw in sym_upper:
            return True
    return False

# ══════════════════════════════════════════════
# FETCH FUNDAMENTALS
# ══════════════════════════════════════════════
def fetch_fundamentals(sym, retries=3):
    for attempt in range(retries):
        try:
            tk   = yf.Ticker(sym + ".NS")
            info = tk.info
            mcap_raw = info.get("marketCap", 0) or 0
            mcap_cr  = round(mcap_raw/10_000_000, 0) if mcap_raw else None
            ebit = info.get("ebit", 0) or 0
            ta_  = info.get("totalAssets", 0) or 0
            tl   = info.get("totalCurrentLiabilities", 0) or 0
            roce = round(ebit/(ta_-tl)*100, 2) if (ta_-tl) > 0 else None
            sector   = info.get("sector", "")
            industry = info.get("industry", "")
            is_fin   = is_financial_stock(sym, sector, industry)
            roa           = round(info.get("returnOnAssets",0)*100,2) if info.get("returnOnAssets") else None
            current_ratio = round(info.get("currentRatio",0),2)       if info.get("currentRatio")   else None
            debt_eq = None
            if not is_fin:
                debt_eq = round(info.get("debtToEquity",0),2) if info.get("debtToEquity") else None
            return {
                "sector":        sector,
                "industry":      industry,
                "is_financial":  is_fin,
                "high52":        info.get("fiftyTwoWeekHigh") or None,
                "low52":         info.get("fiftyTwoWeekLow")  or None,
                "mcap_cr":       mcap_cr,
                "pe":            round(info.get("trailingPE",0),2)         if info.get("trailingPE")        else None,
                "eps":           round(info.get("trailingEps",0),2)        if info.get("trailingEps")       else None,
                "bv":            round(info.get("bookValue",0),2)          if info.get("bookValue")         else None,
                "pb":            round(info.get("priceToBook",0),2)        if info.get("priceToBook")       else None,
                "div":           round(info.get("dividendYield",0)*100,2)  if info.get("dividendYield")     else None,
                "roe":           round(info.get("returnOnEquity",0)*100,2) if info.get("returnOnEquity")    else None,
                "roa":           roa,
                "roce":          roce,
                "debt_eq":       debt_eq,
                "current_ratio": current_ratio,
                "beta":          round(info.get("beta",0),2)               if info.get("beta")              else None,
            }
        except Exception as e:
            if "429" in str(e) or "Too Many" in str(e):
                wait = (attempt+1) * 15
                log.warning(f"  Rate limited {sym}, waiting {wait}s")
                time.sleep(wait)
            else:
                log.warning(f"  fundamentals failed {sym}: {e}")
                return {}
    return {}

def fetch_rev_growth(sym):
    try:
        fin = yf.Ticker(sym+".NS").financials
        if fin is not None and not fin.empty and "Total Revenue" in fin.index:
            rv = fin.loc["Total Revenue"].dropna()
            if len(rv) >= 2:
                return round((rv.iloc[0]-rv.iloc[1])/abs(rv.iloc[1])*100, 2)
    except:
        pass
    return None

# ══════════════════════════════════════════════
# BATCH PRICE FETCH
# ══════════════════════════════════════════════
def fetch_prices_batch(symbols):
    prices  = {}
    ns_syms = [s+".NS" for s in symbols]
    for i in range(0, len(ns_syms), BATCH_SIZE):
        batch      = ns_syms[i:i+BATCH_SIZE]
        batch_orig = symbols[i:i+BATCH_SIZE]
        for attempt in range(3):
            try:
                df = yf.download(
                    tickers=batch, period="5d", interval="1d",
                    group_by="ticker", auto_adjust=True,
                    progress=False, threads=False
                )
                for sym, ns in zip(batch_orig, batch):
                    try:
                        close = df[ns]["Close"].dropna().iloc[-1] if len(batch)>1 else df["Close"].dropna().iloc[-1]
                        prices[sym] = round(float(close), 2)
                    except:
                        prices[sym] = None
                log.info(f"Batch {i//BATCH_SIZE+1}: {len(batch)} prices fetched")
                break
            except Exception as e:
                if "429" in str(e) or "Too Many" in str(e):
                    wait = (attempt+1)*20
                    log.warning(f"Batch rate limited, waiting {wait}s")
                    time.sleep(wait)
                else:
                    log.warning(f"Batch failed: {e}")
                    for sym in batch_orig: prices[sym] = None
                    break
        time.sleep(SLEEP_BATCH)
    return prices

# ══════════════════════════════════════════════
# AI DECISION — SECTOR AWARE
# ══════════════════════════════════════════════
def ai_decision(sym, pe, roe, roa, roce, debt_eq, rev_growth,
                div, ret_pct, is_financial, current_ratio, pb, beta):
    score, reason = 0, []

    if is_financial:
        if roe:
            if   roe>=15: score+=3; reason.append(f"Strong ROE {roe:.1f}%")
            elif roe>=12: score+=2; reason.append(f"Good ROE {roe:.1f}%")
            elif roe>=8:  score+=1
            else:         score-=1; reason.append(f"Weak ROE {roe:.1f}%")
        if roa:
            if   roa>=2.0: score+=3; reason.append(f"Excellent ROA {roa:.1f}%")
            elif roa>=1.5: score+=2; reason.append(f"Good ROA {roa:.1f}%")
            elif roa>=1.0: score+=1
            elif roa<0.5:  score-=2; reason.append(f"Poor ROA {roa:.1f}%")
        if pb:
            if   pb<1.0:  score+=2; reason.append(f"Undervalued P/B {pb:.1f}x")
            elif pb<2.5:  score+=1
            elif pb>4.0:  score-=1; reason.append(f"Expensive P/B {pb:.1f}x")
        if pe and pe>0:
            if   pe<15: score+=1
            elif pe>40: score-=1; reason.append(f"Expensive PE {pe:.1f}")
        if rev_growth:
            if   rev_growth>=15: score+=2; reason.append(f"Strong rev growth {rev_growth:.1f}%")
            elif rev_growth>=8:  score+=1; reason.append(f"Rev growth {rev_growth:.1f}%")
            elif rev_growth<0:   score-=1; reason.append("Revenue declining")
        if div and div>=1.5:
            score+=1; reason.append(f"Div yield {div:.1f}%")
        decision = "HOLD" if score>=5 else ("WATCH" if score>=2 else "SELL")
    else:
        if roe:
            if   roe>=15: score+=2; reason.append(f"Good ROE {roe:.1f}%")
            elif roe<10:  score-=1; reason.append(f"Weak ROE {roe:.1f}%")
        if roce and roce>=15:
            score+=2; reason.append(f"Strong ROCE {roce:.1f}%")
        if debt_eq is not None:
            if   debt_eq<0.3:  score+=2; reason.append("Very low debt")
            elif debt_eq<0.8:  score+=1; reason.append(f"Manageable debt {debt_eq:.1f}x")
            elif debt_eq<1.5:  score-=1; reason.append(f"High debt {debt_eq:.1f}x")
            else:              score-=2; reason.append(f"Dangerous debt {debt_eq:.1f}x")
        if rev_growth:
            if   rev_growth>=10: score+=1; reason.append(f"Rev growth {rev_growth:.1f}%")
            elif rev_growth<0:   score-=1; reason.append("Revenue declining")
        if pe and pe>0:
            if   pe<20: score+=1; reason.append(f"Fair PE {pe:.1f}")
            elif pe>50: score-=1; reason.append(f"Expensive PE {pe:.1f}")
        if div and div>=2:
            score+=1; reason.append(f"Div yield {div:.1f}%")
        decision = "HOLD" if score>=4 else ("WATCH" if score>=1 else "SELL")

    return decision, " | ".join(reason[:3]) if reason else "Insufficient data"

# ══════════════════════════════════════════════
# INDIAN CR FORMAT
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

# ══════════════════════════════════════════════
# COLOR HELPERS
# ══════════════════════════════════════════════
def hex_rgb(h):
    h = h.lstrip("#")
    return {"red":int(h[0:2],16)/255,"green":int(h[2:4],16)/255,"blue":int(h[4:6],16)/255}

def color_cell_req(sheet_id, row_idx, col_idx, bg, fg, bold=True):
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx, "endRowIndex": row_idx+1,
                "startColumnIndex": col_idx, "endColumnIndex": col_idx+1
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
        sh.batch_update({"requests": requests[i:i+chunk]})
        time.sleep(0.2)

# ══════════════════════════════════════════════
# WRITE GITHUB DATA TAB
# ══════════════════════════════════════════════
def write_colab_data(sh, rows):
    try:
        ws = sh.worksheet("GITHUB DATA")
        ws.clear()
    except:
        ws = sh.add_worksheet("GITHUB DATA", rows=300, cols=35)

    headers = [
        "Symbol","CMP","Sector","Industry",
        "52W High","52W Low","% from 52W High",
        "Mkt Cap Cr","Cap Type",
        "PE","EPS","Book Value","P/B",
        "Div Yield%","ROE%","ROCE%","Debt/Equity",
        "Rev Growth%","Beta",
        "AI Decision","AI Reason","XIRR%","Updated",
        "Target Progress%","Sector Median PE",
        "Portfolio Weight%","Swing Rotation",
        # Technical columns
        "RSI","SMA 50","SMA 200","EMA 20",
        "Vol Spike","Trend","Swing Signal"
    ]
    ws.append_row(headers)
    if rows:
        ws.append_rows(rows)

    # Header format
    sh.batch_update({"requests":[{"repeatCell":{
        "range":{"sheetId":ws.id,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":len(headers)},
        "cell":{"userEnteredFormat":{
            "backgroundColor":hex_rgb("0d1b2a"),
            "textFormat":{"foregroundColor":hex_rgb("ffffff"),"bold":True,"fontSize":10},
            "verticalAlignment":"MIDDLE","wrapStrategy":"WRAP"
        }},
        "fields":"userEnteredFormat"
    }}]})

    # Technical headers — different color to distinguish
    sh.batch_update({"requests":[{"repeatCell":{
        "range":{"sheetId":ws.id,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":27,"endColumnIndex":len(headers)},
        "cell":{"userEnteredFormat":{
            "backgroundColor":hex_rgb("1b2a4f"),
            "textFormat":{"foregroundColor":hex_rgb("ffffff"),"bold":True,"fontSize":10},
        }},
        "fields":"userEnteredFormat"
    }}]})

    # Freeze
    sh.batch_update({"requests":[{"updateSheetProperties":{
        "properties":{"sheetId":ws.id,"gridProperties":{"frozenRowCount":1,"frozenColumnCount":1}},
        "fields":"gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }}]})

    # Column widths
    widths = [
        90,90,100,120,90,90,100,130,90,
        60,70,80,65,70,70,70,70,80,60,
        90,220,70,100,120,110,130,160,
        60,80,80,70,80,120,110
    ]
    sh.batch_update({"requests":[{"updateDimensionProperties":{
        "range":{"sheetId":ws.id,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},
        "properties":{"pixelSize":w},"fields":"pixelSize"
    }} for i,w in enumerate(widths)]})

    # Cell colors
    all_out = ws.get_all_values()[1:]
    reqs    = []

    for i, row in enumerate(all_out):
        rn  = i+1
        alt = "f8f9fa" if i%2==0 else "ffffff"
        reqs.append({"repeatCell":{
            "range":{"sheetId":ws.id,"startRowIndex":rn,"endRowIndex":rn+1,"startColumnIndex":0,"endColumnIndex":len(headers)},
            "cell":{"userEnteredFormat":{"backgroundColor":hex_rgb(alt)}},
            "fields":"userEnteredFormat.backgroundColor"
        }})

        def sf(idx):
            try:
                v = str(row[idx]).replace("%","").replace(",","").replace("₹","").replace(" Cr","").strip()
                return float(v) if len(row)>idx and v else None
            except: return None

        cap    = row[8].strip()  if len(row)>8  else ""
        ai     = row[19].strip() if len(row)>19 else ""
        swing  = row[33].strip() if len(row)>33 else ""
        pct    = sf(6);   pe_v  = sf(9);   eps_v = sf(10)
        roe_v  = sf(14);  de_v  = sf(16);  rg_v  = sf(17)
        xirr_v = sf(21);  tgt_v = sf(23);  wt_v  = sf(25)
        rsi_v  = sf(27);  vol_v = sf(31)

        # Cap Type colors
        if   cap=="Large Cap": cb,cf = "d9ead3","0b8043"
        elif cap=="Mid Cap":   cb,cf = "d9eaf7","1565c0"
        elif cap=="Small Cap": cb,cf = "fde9d9","c62828"
        else:                  cb,cf = "ffffff","000000"
        if cap:
            reqs += [
                color_cell_req(ws.id,rn,0,cb,cf),
                color_cell_req(ws.id,rn,7,cb,cf),
                color_cell_req(ws.id,rn,8,cb,cf)
            ]

        # 52W High blue, Low red
        reqs.append(color_cell_req(ws.id,rn,4,"eaf4fb","1565c0",bold=False))
        reqs.append(color_cell_req(ws.id,rn,5,"fdf2f2","c62828",bold=False))

        # % from 52W High
        if pct is not None:
            reqs.append(color_cell_req(ws.id,rn,6,"d9ead3","0b8043") if pct>=-20 else color_cell_req(ws.id,rn,6,"fde9d9","c62828"))

        # PE
        if pe_v and pe_v>0:
            reqs.append(color_cell_req(ws.id,rn,9,"d9ead3","0b8043") if pe_v<20 else color_cell_req(ws.id,rn,9,"fde9d9","c62828"))

        # EPS
        if eps_v is not None:
            thresh = 50 if cap=="Large Cap" else (20 if cap=="Mid Cap" else 5)
            reqs.append(color_cell_req(ws.id,rn,10,"d9ead3","0b8043") if eps_v>=thresh else color_cell_req(ws.id,rn,10,"fde9d9","c62828"))

        # ROE
        if roe_v is not None:
            if   roe_v>=15: reqs.append(color_cell_req(ws.id,rn,14,"d9ead3","0b8043"))
            elif roe_v<10:  reqs.append(color_cell_req(ws.id,rn,14,"fde9d9","c62828"))

        # Debt/Eq
        if de_v is not None:
            if   de_v<0.5:  reqs.append(color_cell_req(ws.id,rn,16,"d9ead3","0b8043"))
            elif de_v>1.5:  reqs.append(color_cell_req(ws.id,rn,16,"fde9d9","c62828"))

        # Rev Growth
        if rg_v is not None:
            if   rg_v>=10: reqs.append(color_cell_req(ws.id,rn,17,"d9ead3","0b8043"))
            elif rg_v<0:   reqs.append(color_cell_req(ws.id,rn,17,"fde9d9","c62828"))

        # AI Decision
        if   ai=="HOLD":  reqs.append(color_cell_req(ws.id,rn,19,"d9ead3","0b8043"))
        elif ai=="WATCH": reqs.append(color_cell_req(ws.id,rn,19,"fff2cc","7f4f00"))
        elif ai=="SELL":  reqs.append(color_cell_req(ws.id,rn,19,"fde9d9","c62828"))

        # XIRR
        if xirr_v is not None:
            reqs.append(color_cell_req(ws.id,rn,21,"d9ead3","0b8043") if xirr_v>=0 else color_cell_req(ws.id,rn,21,"fde9d9","c62828"))

        # Target Progress
        if tgt_v is not None:
            if   tgt_v>=20: reqs.append(color_cell_req(ws.id,rn,23,"00c853","ffffff"))
            elif tgt_v>=10: reqs.append(color_cell_req(ws.id,rn,23,"d9ead3","0b8043"))
            elif tgt_v>=0:  reqs.append(color_cell_req(ws.id,rn,23,"fff2cc","7f4f00"))
            else:           reqs.append(color_cell_req(ws.id,rn,23,"fde9d9","c62828"))

        # Portfolio Weight
        if wt_v and wt_v>0:
            if   wt_v>15: reqs.append(color_cell_req(ws.id,rn,25,"fde9d9","c62828"))
            elif wt_v>7:  reqs.append(color_cell_req(ws.id,rn,25,"fff2cc","7f4f00"))
            else:         reqs.append(color_cell_req(ws.id,rn,25,"d9ead3","0b8043"))

        # RSI color — oversold green, overbought red
        if rsi_v is not None:
            if   rsi_v < 35:  reqs.append(color_cell_req(ws.id,rn,27,"d9ead3","0b8043"))
            elif rsi_v > 70:  reqs.append(color_cell_req(ws.id,rn,27,"fde9d9","c62828"))
            elif rsi_v > 60:  reqs.append(color_cell_req(ws.id,rn,27,"fff2cc","7f4f00"))

        # Volume spike color
        if vol_v is not None:
            if vol_v > 2.0: reqs.append(color_cell_req(ws.id,rn,31,"fff2cc","7f4f00"))
            elif vol_v > 3.0: reqs.append(color_cell_req(ws.id,rn,31,"fde9d9","c62828"))

        # Swing Signal color
        if   "SWING BUY"  in swing: reqs.append(color_cell_req(ws.id,rn,33,"d9ead3","0b8043"))
        elif "SWING SELL" in swing: reqs.append(color_cell_req(ws.id,rn,33,"fde9d9","c62828"))
        elif "NEUTRAL"    in swing: reqs.append(color_cell_req(ws.id,rn,33,"f1f3f4","444444"))
        elif "WATCH"      in swing: reqs.append(color_cell_req(ws.id,rn,33,"fff2cc","7f4f00"))

    batch_update_safe(sh, reqs)
    log.info("GITHUB DATA tab formatted")
    return ws

# ══════════════════════════════════════════════
# WRITE GROWTH SCREENER TAB
# ══════════════════════════════════════════════
def write_growth_screener(sh, all_out):
    rating_colors = {
        "STRONG BUY": ("0b8043","d9ead3"),
        "BUY":        ("1565c0","d9eaf7"),
        "WATCH":      ("7f4f00","fff2cc"),
        "NEUTRAL":    ("444444","f1f3f4"),
        "AVOID":      ("c62828","fde9d9"),
    }
    growth = []

    for row in all_out:
        if not row or not row[0]: continue
        sym = row[0].strip()
        cap = row[8].strip() if len(row)>8 else ""

        def sf(v):
            try: return float(str(v).replace("%","").replace(",","").replace("₹","").replace(" Cr","").strip())
            except: return None

        f_roe  = sf(row[14] if len(row)>14 else "")
        f_roce = sf(row[15] if len(row)>15 else "")
        f_de   = sf(row[16] if len(row)>16 else "")
        f_rev  = sf(row[17] if len(row)>17 else "")
        f_pe   = sf(row[9]  if len(row)>9  else "")
        f_pcthi= sf(row[6]  if len(row)>6  else "")
        f_div  = sf(row[13] if len(row)>13 else "")
        f_rsi  = sf(row[27] if len(row)>27 else "")
        swing  = row[33].strip() if len(row)>33 else ""

        score, notes, flags = 0, [], []

        if f_roe:
            if   f_roe>=20: score+=3; notes.append(f"Excellent ROE {f_roe:.1f}%")
            elif f_roe>=15: score+=2; notes.append(f"Good ROE {f_roe:.1f}%")
            elif f_roe>=10: score+=1
            else:           flags.append(f"Weak ROE {f_roe:.1f}%")
        if f_roce:
            if   f_roce>=20: score+=3; notes.append(f"Strong ROCE {f_roce:.1f}%")
            elif f_roce>=12: score+=2; notes.append(f"Decent ROCE {f_roce:.1f}%")
            elif f_roce>=8:  score+=1
            else:            flags.append(f"Poor ROCE {f_roce:.1f}%")
        if f_rev is not None:
            if   f_rev>=20: score+=3; notes.append(f"High rev growth {f_rev:.1f}%")
            elif f_rev>=10: score+=2; notes.append(f"Rev growth {f_rev:.1f}%")
            elif f_rev>=0:  score+=1
            else:           score-=1; flags.append(f"Revenue declining {f_rev:.1f}%")
        if f_de is not None:
            if   f_de<0.3:  score+=2; notes.append("Very low debt")
            elif f_de<1.0:  score+=1; notes.append(f"Manageable debt {f_de:.1f}x")
            elif f_de<2.0:  score-=1; flags.append(f"High debt {f_de:.1f}x")
            else:           score-=2; flags.append(f"Dangerous debt {f_de:.1f}x")
        if f_pe and f_pe>0:
            if   f_pe<15:  score+=2; notes.append(f"Undervalued PE {f_pe:.1f}")
            elif f_pe<30:  score+=1; notes.append(f"Fair PE {f_pe:.1f}")
            elif f_pe>=50: score-=1; flags.append(f"Expensive PE {f_pe:.1f}")
        if cap=="Large Cap": score+=1
        elif cap=="Small Cap": score-=1
        if f_div and f_div>=2:
            score+=1; notes.append(f"Div yield {f_div:.1f}%")
        if f_pcthi is not None:
            if   f_pcthi>=-10: notes.append("Near 52W high")
            elif f_pcthi<-40:  notes.append(f"Deep correction {f_pcthi:.1f}%")

        # RSI bonus for growth screener
        if f_rsi is not None:
            if   f_rsi < 35: score+=1; notes.append(f"Oversold RSI {f_rsi}")
            elif f_rsi > 70: score-=1; flags.append(f"Overbought RSI {f_rsi}")

        # Swing signal bonus
        if "SWING BUY" in swing:
            score+=1; notes.append("Swing buy setup")

        if   score>=10: rating="STRONG BUY"
        elif score>=7:  rating="BUY"
        elif score>=4:  rating="WATCH"
        elif score>=1:  rating="NEUTRAL"
        else:           rating="AVOID"

        concern   = " | ".join(flags[:2])
        positives = " | ".join(notes[:3]) if notes else "Insufficient data"

        if   rating in ["STRONG BUY","BUY"]: note = f"✅ {positives}" + (f" ⚠️ {concern}" if concern else "")
        elif rating=="WATCH":                 note = f"👀 {positives}" + (f" | Risk: {concern}" if concern else "")
        elif rating=="AVOID":                 note = f"❌ {concern or 'Weak fundamentals'}"
        else:                                 note = f"➡️ {positives}" + (f" | {concern}" if concern else "")

        growth.append([
            sym, row[1] if len(row)>1 else "",
            row[2] if len(row)>2 else "", cap,
            row[9]  if len(row)>9  else "",
            row[14] if len(row)>14 else "",
            row[15] if len(row)>15 else "",
            row[16] if len(row)>16 else "",
            row[17] if len(row)>17 else "",
            row[13] if len(row)>13 else "",
            row[6]  if len(row)>6  else "",
            score, rating, note,
            row[19] if len(row)>19 else "",
            row[27] if len(row)>27 else "",
            row[33] if len(row)>33 else ""
        ])

    growth.sort(key=lambda x: x[11] if isinstance(x[11],(int,float)) else 0, reverse=True)

    try:
        gsw = sh.worksheet("Growth Screener")
        gsw.clear()
    except:
        gsw = sh.add_worksheet("Growth Screener", rows=200, cols=18)

    gsw.append_row([
        "Symbol","CMP","Sector","Cap Type","PE","ROE%","ROCE%",
        "Debt/Eq","Rev Growth%","Div Yield%","% from 52W High",
        "Score","Rating","Analyst Note","AI Decision","RSI","Swing Signal"
    ])
    if growth: gsw.append_rows(growth)

    reqs = [{"repeatCell":{
        "range":{"sheetId":gsw.id,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":17},
        "cell":{"userEnteredFormat":{
            "backgroundColor":hex_rgb("0d1b2a"),
            "textFormat":{"foregroundColor":hex_rgb("ffffff"),"bold":True,"fontSize":10},
            "verticalAlignment":"MIDDLE","wrapStrategy":"WRAP"
        }},
        "fields":"userEnteredFormat"
    }}]
    reqs.append({"updateSheetProperties":{
        "properties":{"sheetId":gsw.id,"gridProperties":{"frozenRowCount":1,"frozenColumnCount":1}},
        "fields":"gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }})
    gs_widths = [90,90,110,90,60,65,65,70,90,80,100,55,100,380,100,60,110]
    reqs += [{"updateDimensionProperties":{
        "range":{"sheetId":gsw.id,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},
        "properties":{"pixelSize":w},"fields":"pixelSize"
    }} for i,w in enumerate(gs_widths)]

    for i, row in enumerate(growth):
        rn = i+1
        alt = "f8f9fa" if i%2==0 else "ffffff"
        fg_hex, bg_hex = rating_colors.get(row[12], ("000000","ffffff"))
        reqs.append({"repeatCell":{
            "range":{"sheetId":gsw.id,"startRowIndex":rn,"endRowIndex":rn+1,"startColumnIndex":0,"endColumnIndex":17},
            "cell":{"userEnteredFormat":{"backgroundColor":hex_rgb(alt)}},
            "fields":"userEnteredFormat.backgroundColor"
        }})
        reqs.append(color_cell_req(gsw.id,rn,12,bg_hex,fg_hex))
        cap = row[3]
        if   cap=="Large Cap": reqs.append(color_cell_req(gsw.id,rn,3,"d9ead3","0b8043"))
        elif cap=="Mid Cap":   reqs.append(color_cell_req(gsw.id,rn,3,"d9eaf7","1565c0"))
        elif cap=="Small Cap": reqs.append(color_cell_req(gsw.id,rn,3,"fde9d9","c62828"))

        # RSI color in growth screener
        try:
            rsi_val = float(str(row[15]).replace("%",""))
            if   rsi_val<35: reqs.append(color_cell_req(gsw.id,rn,15,"d9ead3","0b8043"))
            elif rsi_val>70: reqs.append(color_cell_req(gsw.id,rn,15,"fde9d9","c62828"))
        except: pass

        # Swing signal color
        sw = str(row[16])
        if   "SWING BUY"  in sw: reqs.append(color_cell_req(gsw.id,rn,16,"d9ead3","0b8043"))
        elif "SWING SELL" in sw: reqs.append(color_cell_req(gsw.id,rn,16,"fde9d9","c62828"))
        elif "NEUTRAL"    in sw: reqs.append(color_cell_req(gsw.id,rn,16,"f1f3f4","444444"))

    reqs.append({"repeatCell":{
        "range":{"sheetId":gsw.id,"startRowIndex":1,"endRowIndex":len(growth)+1,"startColumnIndex":13,"endColumnIndex":14},
        "cell":{"userEnteredFormat":{"wrapStrategy":"WRAP"}},
        "fields":"userEnteredFormat.wrapStrategy"
    }})
    batch_update_safe(sh, reqs)
    log.info(f"Growth Screener: {len(growth)} stocks")
    return growth

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def main():
    log.info("═"*55)
    log.info("SIDDEGOWDA PORTFOLIO — Daily Auto-Update")
    log.info(f"Run time: {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}")
    log.info("═"*55)

    gc = get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    log.info("Connected to Google Sheets")

    symbols = read_symbols(sh)
    if not symbols:
        log.error("No symbols found. Exiting.")
        send_telegram("❌ Portfolio update FAILED — no symbols found in Portfolio tab col B")
        return

    trades = read_trades(sh)
    log.info(f"Found {len(symbols)} symbols")

    # ── Batch price fetch ──────────────────────
    log.info("Fetching prices...")
    prices = fetch_prices_batch(symbols)

    # ── Fundamentals + Technicals ──────────────
    log.info("Fetching fundamentals + technicals...")
    fund_map   = {}
    tech_map   = {}
    rev_map    = {}
    ind_pe_map = {}

    for sym in symbols:
        f = fetch_fundamentals(sym)
        fund_map[sym] = f
        rev_map[sym]  = fetch_rev_growth(sym)

        # Technical indicators
        log.info(f"  Technicals: {sym}")
        tech_map[sym] = fetch_technicals(sym)

        ind = f.get("industry","")
        pe  = f.get("pe")
        if ind and pe and pe > 0:
            ind_pe_map.setdefault(ind,[]).append(pe)
        time.sleep(SLEEP_INFO)

    sector_med = {
        ind: round(statistics.median(v),2)
        for ind,v in ind_pe_map.items() if v
    }

    # ── Portfolio value ────────────────────────
    holdings             = {}
    portfolio_live_value = 0.0
    for sym in symbols:
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        cmp = prices.get(sym)
        if qty > 0 and cmp and cmp > 0:
            holdings[sym]         = (qty, cmp, avg_buy)
            portfolio_live_value += qty * cmp

    owned = set(holdings.keys())

    # ── Build results + collect alerts ────────
    results = []
    failed  = []
    alerts  = {
        "sl_breach":  [],
        "target_hit": [],
        "sell_signal":[],
        "swing_buy":  [],
        "strong_buy": []
    }

    SL_PCT     = 0.07   # 7% stop loss
    TARGET_PCT = 0.20   # 20% target

    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            failed.append(sym)
            log.warning(f"  SKIP {sym} — no price")
            continue

        f        = fund_map.get(sym, {})
        tech     = tech_map.get(sym, {})
        rev_gr   = rev_map.get(sym)
        high52   = f.get("high52")
        low52    = f.get("low52")
        mcap_cr  = f.get("mcap_cr")
        pe       = f.get("pe");    eps  = f.get("eps")
        bv       = f.get("bv");    pb   = f.get("pb")
        div      = f.get("div");   roe  = f.get("roe")
        roa      = f.get("roa");   roce = f.get("roce")
        de       = f.get("debt_eq"); beta= f.get("beta")
        cr       = f.get("current_ratio")
        sector   = f.get("sector","")
        industry = f.get("industry","")
        is_fin   = f.get("is_financial", False)

        cap_type = ""
        if mcap_cr:
            if   mcap_cr>=25000: cap_type="Large Cap"
            elif mcap_cr>=5000:  cap_type="Mid Cap"
            else:                cap_type="Small Cap"

        pct_high = round((cmp-high52)/high52*100,2) if high52 else ""
        mcap_fmt = indian_cr(mcap_cr) if mcap_cr else ""

        xirr_val = get_xirr(sym, trades, cmp)

        ret_pct = None
        for t in trades:
            if t[0].strip().upper()==sym and t[2].strip().upper()=="BUY":
                try: ret_pct=(cmp-float(t[4]))/float(t[4])*100
                except: pass

        decision, reason = ai_decision(
            sym, pe, roe, roa, roce, de, rev_gr, div,
            ret_pct, is_fin, cr, pb, beta
        )

        # Enhancements
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        tgt_progress = round(((cmp-avg_buy)/avg_buy)*100,2) if avg_buy and qty>0 else ""
        med_pe       = sector_med.get(industry,"")
        port_weight  = round((qty*cmp/portfolio_live_value)*100,2) if sym in owned and portfolio_live_value>0 else ""

        swing_rot = ""
        if sym not in owned and pct_high!="" and pct_high<=-25 and decision in ("HOLD","WATCH"):
            swing_rot = "🎯 BUY ZONE (VALUE)"

        de_display = de if not is_fin else ""

        # Technical data
        rsi         = tech.get("rsi","")
        sma50       = tech.get("sma50","")
        sma200      = tech.get("sma200","")
        ema20       = tech.get("ema20","")
        vol_spike   = tech.get("vol_spike","")
        trend       = tech.get("trend","")
        swing_sig   = tech.get("swing_signal","")
        swing_rsn   = tech.get("swing_reason","")

        # ── ALERT DETECTION ───────────────────
        if avg_buy and qty > 0:
            sl_price  = avg_buy * (1 - SL_PCT)
            tgt_price = avg_buy * (1 + TARGET_PCT)

            if cmp <= sl_price:
                alerts["sl_breach"].append({
                    "sym": sym, "cmp": cmp,
                    "sl": round(sl_price,2)
                })

            if cmp >= tgt_price:
                alerts["target_hit"].append({
                    "sym": sym, "cmp": cmp,
                    "tgt": round(tgt_price,2)
                })

        if decision == "SELL":
            alerts["sell_signal"].append({
                "sym": sym, "reason": reason
            })

        if swing_sig == "SWING BUY":
            alerts["swing_buy"].append({
                "sym": sym,
                "rsi": rsi,
                "reason": swing_rsn
            })

        results.append([
            sym, cmp, sector, industry,
            high52 or "", low52 or "", pct_high,
            mcap_fmt, cap_type,
            pe or "", eps or "", bv or "", pb or "",
            div or "", roe or "", roce or "", de_display,
            rev_gr or "", beta or "",
            decision, reason,
            xirr_val if xirr_val else "",
            datetime.now().strftime("%d-%b-%Y %H:%M"),
            tgt_progress, med_pe, port_weight, swing_rot,
            # Technical columns
            rsi, sma50, sma200, ema20,
            vol_spike, trend,
            f"{swing_sig} — {swing_rsn}" if swing_sig and swing_rsn else swing_sig
        ])

        fin_tag = " [FIN]" if is_fin else ""
        log.info(f"  OK  {sym:12} ₹{cmp:>8} | {cap_type:10} | RSI:{rsi} | {swing_sig:12} | {decision}{fin_tag}")

    # ── Write sheets ───────────────────────────
    ws      = write_colab_data(sh, results)
    all_out = ws.get_all_values()[1:]
    growth  = write_growth_screener(sh, all_out)

    # ── Collect strong buys for Telegram ──────
    for r in growth:
        if r[12] in ("STRONG BUY","BUY"):
            alerts["strong_buy"].append({
                "sym":    r[0],
                "score":  r[11],
                "reason": r[13][:60]
            })

    # ── Send Telegram alert ───────────────────
    msg = build_alert_message(alerts, portfolio_live_value, growth)
    send_telegram(msg)

    # ── Summary ────────────────────────────────
    swing_buys = [r[0] for r in results if "SWING BUY" in str(r[33])]
    exit_ready = [r[0] for r in results if isinstance(r[23],(int,float)) and r[23]>=20]

    log.info("═"*55)
    log.info(f"✅ {len(results)} stocks updated | ❌ Failed: {failed or 'None'}")
    log.info(f"💰 Portfolio: ₹{portfolio_live_value:,.0f}")
    log.info(f"🔴 SL Breach: {[a['sym'] for a in alerts['sl_breach']] or 'None'}")
    log.info(f"🎯 Target Hit: {[a['sym'] for a in alerts['target_hit']] or 'None'}")
    log.info(f"📈 Swing Buy: {swing_buys or 'None'}")
    log.info(f"🚀 Exit Ready: {exit_ready or 'None'}")
    log.info(f"Top 5 growth picks:")
    for r in growth[:5]:
        log.info(f"   {r[0]:<12} Score:{r[11]:>3}  {r[12]:<12} RSI:{r[15]}")
    log.info("═"*55)

if __name__ == "__main__":
    main()
