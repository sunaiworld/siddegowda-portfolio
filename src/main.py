#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — GitHub Actions Auto-Updater
Runs daily after NSE market close (Mon-Fri 16:00 IST)
"""

import os
import json
import time
import logging
import statistics
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
SHEET_ID     = os.environ.get("SHEET_ID", "")
SCOPES       = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
BATCH_SIZE   = 10    # symbols per yf.download() batch
SLEEP_BATCH  = 2     # seconds between batches
SLEEP_INFO   = 0.3   # seconds between .info calls

# ══════════════════════════════════════════════
# GOOGLE SHEETS AUTH
# ══════════════════════════════════════════════
def get_gspread_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON secret not set.")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)

# ══════════════════════════════════════════════
# READ SYMBOLS FROM SHEET
# ══════════════════════════════════════════════
def read_symbols(sh):
    skip = {"TOTAL","SYMBOL","SUM","SUBTOTAL","GRAND","NA","N/A",""}
    symbols = []

    # Portfolio col A
    try:
        pws  = sh.worksheet("Portfolio")
        rows = pws.get_all_values()[1:]
        for row in rows:
            sym = row[0].strip().upper() if row else ""
            if sym and sym not in symbols and sym not in skip and len(sym)<=15 and sym.replace("&","").isalnum():
                symbols.append(sym)
        log.info(f"Portfolio tab: {len(symbols)} symbols")
    except Exception as e:
        log.warning(f"Could not read Portfolio tab: {e}")

    # Trade Log col A (symbol col in new structure)
    try:
        tws        = sh.worksheet("Trade Log")
        trade_rows = tws.get_all_values()[1:]
        for row in trade_rows:
            sym = row[0].strip().upper() if row else ""
            if sym and sym not in symbols and sym not in skip and len(sym)<=15 and sym.replace("&","").isalnum():
                symbols.append(sym)
        log.info(f"After Trade Log merge: {len(symbols)} total symbols")
    except Exception as e:
        log.warning(f"Could not read Trade Log tab: {e}")

    return symbols

# ══════════════════════════════════════════════
# READ TRADES FOR XIRR + AVG BUY
# ══════════════════════════════════════════════
def read_trades(sh):
    try:
        tws    = sh.worksheet("Trade Log")
        return tws.get_all_values()[1:]
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
    if len(cashflows) < 2:
        return None
    rate = 0.1
    for _ in range(100):
        t0  = dates[0].toordinal()
        try:
            fv  = sum(cf / (1+rate)**((d.toordinal()-t0)/365.25) for cf,d in zip(cashflows,dates))
            dfv = sum(-((d.toordinal()-t0)/365.25)*cf / (1+rate)**((d.toordinal()-t0)/365.25+1) for cf,d in zip(cashflows,dates))
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
# BATCH PRICE FETCH — fast, minimal API calls
# ══════════════════════════════════════════════
def fetch_prices_batch(symbols):
    """
    Use yf.download() to get closing prices for all symbols at once.
    Much faster than calling .info per symbol.
    Returns dict: { sym: latest_close_price }
    """
    prices = {}
    ns_syms = [s + ".NS" for s in symbols]

    for i in range(0, len(ns_syms), BATCH_SIZE):
        batch = ns_syms[i:i+BATCH_SIZE]
        batch_orig = symbols[i:i+BATCH_SIZE]
        try:
            df = yf.download(
                tickers  = batch,
                period   = "5d",
                interval = "1d",
                group_by = "ticker",
                auto_adjust = True,
                progress = False,
                threads  = True
            )
            for sym, ns in zip(batch_orig, batch):
                try:
                    if len(batch) == 1:
                        close = df["Close"].dropna().iloc[-1]
                    else:
                        close = df[ns]["Close"].dropna().iloc[-1]
                    prices[sym] = round(float(close), 2)
                except:
                    prices[sym] = None
            log.info(f"Batch {i//BATCH_SIZE+1}: fetched {len(batch)} prices")
        except Exception as e:
            log.warning(f"Batch download failed: {e}")
            for sym in batch_orig:
                prices[sym] = None
        time.sleep(SLEEP_BATCH)

    return prices

# ══════════════════════════════════════════════
# FETCH FUNDAMENTALS — one .info call per symbol
# ══════════════════════════════════════════════
def fetch_fundamentals(sym):
    """Single .info call — returns dict of fundamentals."""
    try:
        info = yf.Ticker(sym + ".NS").info
        mcap_raw = info.get("marketCap", 0) or 0
        mcap_cr  = round(mcap_raw / 10_000_000, 0) if mcap_raw else None

        ebit = info.get("ebit", 0) or 0
        ta   = info.get("totalAssets", 0) or 0
        tl   = info.get("totalCurrentLiabilities", 0) or 0
        roce = round(ebit/(ta-tl)*100, 2) if (ta-tl) > 0 else None

        return {
            "sector":    info.get("sector",""),
            "industry":  info.get("industry",""),
            "high52":    info.get("fiftyTwoWeekHigh") or None,
            "low52":     info.get("fiftyTwoWeekLow") or None,
            "mcap_cr":   mcap_cr,
            "pe":        round(info.get("trailingPE",0),2)        if info.get("trailingPE")        else None,
            "eps":       round(info.get("trailingEps",0),2)       if info.get("trailingEps")       else None,
            "bv":        round(info.get("bookValue",0),2)         if info.get("bookValue")         else None,
            "pb":        round(info.get("priceToBook",0),2)       if info.get("priceToBook")       else None,
            "div":       round(info.get("dividendYield",0)*100,2) if info.get("dividendYield")     else None,
            "roe":       round(info.get("returnOnEquity",0)*100,2)if info.get("returnOnEquity")    else None,
            "roce":      roce,
            "debt_eq":   round(info.get("debtToEquity",0),2)      if info.get("debtToEquity")      else None,
            "beta":      round(info.get("beta",0),2)              if info.get("beta")              else None,
        }
    except Exception as e:
        log.warning(f"  fundamentals failed {sym}: {e}")
        return {}

def fetch_rev_growth(sym):
    """Separate call for revenue growth — uses financials endpoint."""
    try:
        fin = yf.Ticker(sym + ".NS").financials
        if fin is not None and not fin.empty and "Total Revenue" in fin.index:
            rv = fin.loc["Total Revenue"].dropna()
            if len(rv) >= 2:
                return round((rv.iloc[0]-rv.iloc[1])/abs(rv.iloc[1])*100, 2)
    except:
        pass
    return None

# ══════════════════════════════════════════════
# AI DECISION LOGIC
# ══════════════════════════════════════════════
def ai_decision(pe, roe, roce, debt_eq, rev_growth, div, ret_pct):
    score, reason = 0, []
    if roe:
        if roe>=15:  score+=2; reason.append(f"Good ROE {roe:.1f}%")
        elif roe<10: score-=1; reason.append(f"Weak ROE {roe:.1f}%")
    if roce and roce>=15:
        score+=2; reason.append(f"Strong ROCE {roce:.1f}%")
    if debt_eq is not None:
        if debt_eq<0.5:  score+=1; reason.append("Low debt")
        elif debt_eq>1.5:score-=2; reason.append(f"High debt {debt_eq:.1f}x")
    if rev_growth:
        if rev_growth>=10: score+=1; reason.append(f"Rev growth {rev_growth:.1f}%")
        elif rev_growth<0: score-=1; reason.append("Revenue declining")
    if pe and pe>0:
        if pe<20:  score+=1; reason.append(f"Fair PE {pe:.1f}")
        elif pe>50:score-=1; reason.append(f"Expensive PE {pe:.1f}")
    if ret_pct and ret_pct<-25:
        reason.append("Large drawdown")
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
        last3 = s[-3:]
        rest  = s[:-3]
        parts = []
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
# WRITE + FORMAT COLAB DATA TAB
# ══════════════════════════════════════════════
def write_colab_data(sh, rows):
    try:
        ws = sh.worksheet("Colab Data")
        ws.clear()
    except:
        ws = sh.add_worksheet("Colab Data", rows=300, cols=30)

    headers = [
        "Symbol","CMP","Sector","Industry",
        "52W High","52W Low","% from 52W High",
        "Mkt Cap Cr","Cap Type",
        "PE","EPS","Book Value","P/B",
        "Div Yield%","ROE%","ROCE%","Debt/Equity",
        "Rev Growth%","Beta",
        "AI Decision","AI Reason","XIRR%","Updated",
        "Target Progress%","Sector Median PE",
        "Portfolio Weight%","Swing Rotation"
    ]
    ws.append_row(headers)
    if rows:
        ws.append_rows(rows)

    # ── Header format ──────────────────────────
    sh.batch_update({"requests":[{"repeatCell":{
        "range":{"sheetId":ws.id,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":27},
        "cell":{"userEnteredFormat":{
            "backgroundColor":hex_rgb("0d1b2a"),
            "textFormat":{"foregroundColor":hex_rgb("ffffff"),"bold":True,"fontSize":10},
            "verticalAlignment":"MIDDLE","wrapStrategy":"WRAP"
        }},
        "fields":"userEnteredFormat"
    }}]})

    # ── Freeze ────────────────────────────────
    sh.batch_update({"requests":[{"updateSheetProperties":{
        "properties":{"sheetId":ws.id,"gridProperties":{"frozenRowCount":1,"frozenColumnCount":1}},
        "fields":"gridProperties.frozenRowCount,gridProperties.frozenColumnCount"
    }}]})

    # ── Column widths ─────────────────────────
    widths = [90,90,100,120,90,90,100,130,90,60,70,80,65,70,70,70,70,80,60,90,220,70,100,120,110,130,160]
    sh.batch_update({"requests":[{"updateDimensionProperties":{
        "range":{"sheetId":ws.id,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},
        "properties":{"pixelSize":w},"fields":"pixelSize"
    }} for i,w in enumerate(widths)]})

    # ── Cell colors ───────────────────────────
    all_data = ws.get_all_values()[1:]
    reqs = []

    for i, row in enumerate(all_data):
        rn = i+1
        alt = "f8f9fa" if i%2==0 else "ffffff"
        reqs.append({"repeatCell":{
            "range":{"sheetId":ws.id,"startRowIndex":rn,"endRowIndex":rn+1,"startColumnIndex":0,"endColumnIndex":27},
            "cell":{"userEnteredFormat":{"backgroundColor":hex_rgb(alt)}},
            "fields":"userEnteredFormat.backgroundColor"
        }})

        def sf(idx):
            try: return float(str(row[idx]).replace("%","").replace(",","").replace("₹","").replace(" Cr","").strip()) if len(row)>idx and row[idx] else None
            except: return None

        cap    = row[8].strip()  if len(row)>8  else ""
        ai     = row[19].strip() if len(row)>19 else ""
        pct    = sf(6);  mcap_v = sf(7);   pe_v  = sf(9)
        eps_v  = sf(10); roe_v  = sf(14);  de_v  = sf(16)
        rg_v   = sf(17); xirr_v = sf(21);  tgt_v = sf(23)
        med_pe = sf(24); wt_v   = sf(25)
        swing  = row[26].strip() if len(row)>26 else ""

        # Cap Type colors — Symbol + Mkt Cap + Cap Type cols
        if   cap=="Large Cap": cb,cf = "d9ead3","0b8043"
        elif cap=="Mid Cap":   cb,cf = "d9eaf7","1565c0"
        elif cap=="Small Cap": cb,cf = "fde9d9","c62828"
        else:                  cb,cf = "ffffff","000000"
        if cap:
            reqs += [color_cell_req(ws.id,rn,0,cb,cf),
                     color_cell_req(ws.id,rn,7,cb,cf),
                     color_cell_req(ws.id,rn,8,cb,cf)]

        # 52W High blue, 52W Low red
        reqs.append(color_cell_req(ws.id,rn,4,"eaf4fb","1565c0",bold=False))
        reqs.append(color_cell_req(ws.id,rn,5,"fdf2f2","c62828",bold=False))

        # % from 52W High — green >= -20, red < -20
        if pct is not None:
            reqs.append(color_cell_req(ws.id,rn,6,"d9ead3","0b8043") if pct>=-20 else color_cell_req(ws.id,rn,6,"fde9d9","c62828"))

        # PE — green < 20, red >= 20
        if pe_v and pe_v>0:
            reqs.append(color_cell_req(ws.id,rn,9,"d9ead3","0b8043") if pe_v<20 else color_cell_req(ws.id,rn,9,"fde9d9","c62828"))

        # EPS by cap type
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

        # Sector Median PE
        if pe_v and med_pe and pe_v>0 and med_pe>0:
            reqs.append(color_cell_req(ws.id,rn,24,"d9ead3","0b8043") if pe_v<med_pe else color_cell_req(ws.id,rn,24,"fde9d9","c62828"))

        # Portfolio Weight
        if wt_v and wt_v>0:
            if   wt_v>15: reqs.append(color_cell_req(ws.id,rn,25,"fde9d9","c62828"))
            elif wt_v>7:  reqs.append(color_cell_req(ws.id,rn,25,"fff2cc","7f4f00"))
            else:         reqs.append(color_cell_req(ws.id,rn,25,"d9ead3","0b8043"))

        # Swing Rotation
        if "BUY ZONE" in swing: reqs.append(color_cell_req(ws.id,rn,26,"00c853","ffffff"))
        elif "DEEP DIP" in swing: reqs.append(color_cell_req(ws.id,rn,26,"fff2cc","7f4f00"))

    batch_update_safe(sh, reqs)
    log.info("Colab Data tab formatted")
    return ws

# ══════════════════════════════════════════════
# WRITE + FORMAT GROWTH SCREENER TAB
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
        sym=row[0].strip(); cap=row[8].strip() if len(row)>8 else ""

        def sf(v):
            try: return float(str(v).replace("%","").replace(",","").replace("₹","").replace(" Cr","").strip())
            except: return None

        f_roe=sf(row[14] if len(row)>14 else "")
        f_roce=sf(row[15] if len(row)>15 else "")
        f_de=sf(row[16] if len(row)>16 else "")
        f_rev=sf(row[17] if len(row)>17 else "")
        f_pe=sf(row[9] if len(row)>9 else "")
        f_pcthi=sf(row[6] if len(row)>6 else "")
        f_div=sf(row[13] if len(row)>13 else "")

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
            if   f_pcthi>=-10:  notes.append("Near 52W high — strong momentum")
            elif f_pcthi<-40:   notes.append(f"Deep correction {f_pcthi:.1f}% — potential entry")

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
            sym, row[1], row[2], cap,
            row[9] if len(row)>9 else "",
            row[14] if len(row)>14 else "",
            row[15] if len(row)>15 else "",
            row[16] if len(row)>16 else "",
            row[17] if len(row)>17 else "",
            row[13] if len(row)>13 else "",
            row[6]  if len(row)>6  else "",
            score, rating, note,
            row[19] if len(row)>19 else ""
        ])

    growth.sort(key=lambda x: x[11] if isinstance(x[11],(int,float)) else 0, reverse=True)

    try:
        gsw = sh.worksheet("Growth Screener")
        gsw.clear()
    except:
        gsw = sh.add_worksheet("Growth Screener", rows=200, cols=16)

    gsw.append_row(["Symbol","CMP","Sector","Cap Type","PE","ROE%","ROCE%","Debt/Eq","Rev Growth%","Div Yield%","% from 52W High","Score","Rating","Analyst Note","AI Decision"])
    if growth: gsw.append_rows(growth)

    reqs = [{"repeatCell":{
        "range":{"sheetId":gsw.id,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":15},
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

    gs_widths=[90,90,110,90,60,65,65,70,90,80,100,55,100,380,100]
    reqs += [{"updateDimensionProperties":{
        "range":{"sheetId":gsw.id,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},
        "properties":{"pixelSize":w},"fields":"pixelSize"
    }} for i,w in enumerate(gs_widths)]

    for i, row in enumerate(growth):
        rn = i+1
        alt = "f8f9fa" if i%2==0 else "ffffff"
        fg_hex,bg_hex = rating_colors.get(row[12],("000000","ffffff"))
        reqs.append({"repeatCell":{
            "range":{"sheetId":gsw.id,"startRowIndex":rn,"endRowIndex":rn+1,"startColumnIndex":0,"endColumnIndex":15},
            "cell":{"userEnteredFormat":{"backgroundColor":hex_rgb(alt)}},
            "fields":"userEnteredFormat.backgroundColor"
        }})
        reqs.append(color_cell_req(gsw.id,rn,12,bg_hex,fg_hex))
        cap=row[3]
        if   cap=="Large Cap": reqs.append(color_cell_req(gsw.id,rn,3,"d9ead3","0b8043"))
        elif cap=="Mid Cap":   reqs.append(color_cell_req(gsw.id,rn,3,"d9eaf7","1565c0"))
        elif cap=="Small Cap": reqs.append(color_cell_req(gsw.id,rn,3,"fde9d9","c62828"))

    reqs.append({"repeatCell":{
        "range":{"sheetId":gsw.id,"startRowIndex":1,"endRowIndex":len(growth)+1,"startColumnIndex":13,"endColumnIndex":14},
        "cell":{"userEnteredFormat":{"wrapStrategy":"WRAP"}},
        "fields":"userEnteredFormat.wrapStrategy"
    }})

    batch_update_safe(sh, reqs)
    log.info(f"Growth Screener written: {len(growth)} stocks")
    return growth

# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def main():
    log.info("═"*55)
    log.info("SIDDEGOWDA PORTFOLIO — Daily Auto-Update")
    log.info(f"Run time: {datetime.now().strftime('%d-%b-%Y %H:%M:%S IST')}")
    log.info("═"*55)

    # ── Auth ──────────────────────────────────
    gc = get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    log.info("Connected to Google Sheets")

    # ── Read data ─────────────────────────────
    symbols = read_symbols(sh)
    if not symbols:
        log.error("No symbols found. Exiting.")
        return
    trades = read_trades(sh)
    log.info(f"Symbols: {symbols}")

    # ── Step 1: Batch price fetch ─────────────
    log.info("Fetching prices (batch)...")
    prices = fetch_prices_batch(symbols)

    # ── Step 2: Fundamentals per symbol ───────
    log.info("Fetching fundamentals...")
    fund_map    = {}
    rev_map     = {}
    ind_pe_map  = {}

    for sym in symbols:
        f = fetch_fundamentals(sym)
        fund_map[sym] = f
        rev_map[sym]  = fetch_rev_growth(sym)
        # Collect industry PE for median calculation
        ind = f.get("industry","")
        pe  = f.get("pe")
        if ind and pe and pe > 0:
            ind_pe_map.setdefault(ind,[]).append(pe)
        time.sleep(SLEEP_INFO)

    # Sector median PE
    sector_med = {ind: round(statistics.median(v),2) for ind,v in ind_pe_map.items() if v}

    # ── Step 3: Portfolio live value ──────────
    holdings = {}
    portfolio_live_value = 0.0
    for sym in symbols:
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        cmp = prices.get(sym)
        if qty > 0 and cmp and cmp > 0:
            holdings[sym]        = (qty, cmp, avg_buy)
            portfolio_live_value += qty * cmp

    owned = set(holdings.keys())

    # ── Step 4: Build output rows ─────────────
    results, failed = [], []

    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            failed.append(sym)
            log.warning(f"  SKIP {sym} — no price")
            continue

        f        = fund_map.get(sym, {})
        rev_gr   = rev_map.get(sym)
        high52   = f.get("high52")
        low52    = f.get("low52")
        mcap_cr  = f.get("mcap_cr")
        pe       = f.get("pe");    eps  = f.get("eps")
        bv       = f.get("bv");    pb   = f.get("pb")
        div      = f.get("div");   roe  = f.get("roe")
        roce     = f.get("roce");  de   = f.get("debt_eq")
        beta     = f.get("beta")
        sector   = f.get("sector",""); industry = f.get("industry","")

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

        decision, reason = ai_decision(pe,roe,roce,de,rev_gr,div,ret_pct)

        # Enhancements
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        tgt_progress = round(((cmp-avg_buy)/avg_buy)*100,2) if avg_buy and qty>0 else ""
        med_pe       = sector_med.get(industry,"")
        port_weight  = round((qty*cmp/portfolio_live_value)*100,2) if sym in owned and portfolio_live_value>0 else ""
        swing = ""
        if sym not in owned and pct_high!="" and pct_high<=-25 and decision in ("HOLD","WATCH"):
            swing = "🎯 BUY ZONE (VALUE)"
        elif sym not in owned and pct_high!="" and pct_high<=-25:
            swing = "⚠️ DEEP DIP — CHECK"

        results.append([
            sym, cmp, sector, industry,
            high52 or "", low52 or "", pct_high,
            mcap_fmt, cap_type,
            pe or "", eps or "", bv or "", pb or "",
            div or "", roe or "", roce or "", de or "",
            rev_gr or "", beta or "",
            decision, reason,
            xirr_val if xirr_val else "",
            datetime.now().strftime("%d-%b-%Y %H:%M"),
            tgt_progress, med_pe, port_weight, swing
        ])
        log.info(f"  OK  {sym:12} ₹{cmp:>8} | {cap_type:10} | PE:{pe} | ROE:{roe}% | {decision}")

    # ── Step 5: Write sheets ──────────────────
    ws  = write_colab_data(sh, results)
    all_out = ws.get_all_values()[1:]
    growth  = write_growth_screener(sh, all_out)

    # ── Summary ───────────────────────────────
    exit_ready  = [r[0] for r in results if isinstance(r[23],(int,float)) and r[23]>=20]
    overweight  = [r[0] for r in results if isinstance(r[25],(int,float)) and r[25]>15]
    buy_zone    = [r[0] for r in results if "BUY ZONE" in str(r[26])]

    log.info("═"*55)
    log.info(f"✅ {len(results)} stocks updated | ❌ Failed: {failed or 'None'}")
    log.info(f"💰 Portfolio value: ₹{portfolio_live_value:,.0f}")
    log.info(f"🚀 Exit ready (≥20%): {exit_ready or 'None'}")
    log.info(f"⚠️  Overweight (>15%): {overweight or 'None'}")
    log.info(f"🎯 Buy zones: {buy_zone or 'None'}")
    log.info(f"Top 5 growth picks:")
    for r in growth[:5]:
        log.info(f"   {r[0]:<12} Score:{r[11]:>3}  {r[12]:<12} {r[13][:40]}")
    log.info("═"*55)

if __name__ == "__main__":
    main()
