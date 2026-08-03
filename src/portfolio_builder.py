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
def get_entry_date(sym, trades):
    earliest = None
    for t in trades:
        if not t[0]:
            continue
        if t[0].strip().upper() != sym:
            continue
        try:
            if t[2].strip().upper() != "BUY":
                continue
            raw = str(t[1]).strip()
            dt = None
            for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"]:
                try:
                    dt = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    pass
            if dt and (earliest is None or dt < earliest):
                earliest = dt
        except Exception:
            continue
    return earliest

from score_engine import score_symbol


def compute_risk_score(f, tech, score):
    risk = 0.0

    debt_eq = f.get("debt_eq")
    try:
        debt_eq = float(debt_eq) if debt_eq not in (None, "") else None
    except (TypeError, ValueError):
        debt_eq = None
    if debt_eq is not None:
        if debt_eq >= 2:
            risk += 25
        elif debt_eq >= 1.5:
            risk += 18
        elif debt_eq >= 1:
            risk += 10
        elif debt_eq >= 0.5:
            risk += 5

    v_sc = score.get("valuation")
    if isinstance(v_sc, (int, float)):
        risk += max(0, min(20, 20 - v_sc))
    else:
        risk += 5

    rsi = tech.get("rsi")
    try:
        rsi = float(rsi) if rsi not in (None, "") else None
    except (TypeError, ValueError):
        rsi = None
    if rsi is not None:
        if rsi >= 70 or rsi <= 30:
            risk += 10
        else:
            risk += max(0, min(10, abs(rsi - 50) / 5))

    trend = str(tech.get("trend", "")).strip().lower()
    is_bearish = "down" in trend or "bear" in trend
    is_bullish = "up" in trend or "bull" in trend
    if is_bearish:
        risk += 10

    vol_spike = tech.get("vol_spike")
    try:
        vol_spike = float(vol_spike) if vol_spike not in (None, "") else None
    except (TypeError, ValueError):
        vol_spike = None
    if vol_spike is not None and vol_spike >= 2:
        if is_bearish:
            risk += 10
        elif is_bullish:
            risk += 3

    weaknesses = score.get("weaknesses") or []
    risk += min(20, len(weaknesses) * 4)

    risk_score = int(round(max(0, min(100, risk))))
    risk_level = "Low" if risk_score <= 29 else "Medium" if risk_score <= 59 else "High"
    return risk_score, risk_level


def build_portfolio(symbols, trades, prices, fund_map, tech_map, rev_map, nc_cache=None):
    nc_cache = nc_cache or {}

    prelim, portfolio_live_value = {}, 0.0
    for sym in symbols:
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        cmp = prices.get(sym)
        if qty > 0 and cmp and cmp > 0:
            prelim[sym] = (avg_buy, qty, cmp)
            portfolio_live_value += qty * cmp

    rows = []
    for sym in symbols:
        if sym not in prelim:
            continue
        avg_buy, qty, cmp = prelim[sym]

        invested = round(avg_buy * qty, 2) if avg_buy else None
        value    = round(cmp * qty, 2)
        pnl      = round(value - invested, 2) if invested is not None else None
        ret_pct  = round((pnl / invested) * 100, 2) if invested else None
        wt_pct   = round((value / portfolio_live_value) * 100, 2) if portfolio_live_value else 0
        wt_status = "Underweight" if wt_pct < 2 else "Normal" if wt_pct <= 6 else "Overweight"

        sl_price = round(avg_buy * (1 - SL_PCT), 2) if avg_buy else None
        target   = round(avg_buy * (1 + TARGET_PCT), 2) if avg_buy else None
        buy_more = round(avg_buy * 0.90, 2) if avg_buy else None

        f, tech, rev_gr = fund_map.get(sym, {}), tech_map.get(sym, {}), rev_map.get(sym)
        nd = nc_cache.get(sym.upper(), {})
        score = score_symbol(sym, cmp, f, tech, rev_gr, news_data=nd)
        risk_score, risk_level = compute_risk_score(f, tech, score)

        rows.append({
            "symbol": sym,
            "avg_buy": avg_buy, "shares": qty,
            "invested": invested, "value": value,
            "pnl": pnl, "return_pct": ret_pct,
            "wt_pct": wt_pct, "wt_status": wt_status,
            "sl_price": sl_price, "target": target, "buy_more": buy_more,
            "signal": score["final_action"],
            "risk_score": risk_score, "risk_level": risk_level,
        })
    return rows


def write_portfolio(sh, portfolio_rows, tab_name="Portfolio"):
    ws = sh.worksheet(tab_name)
    all_rows = ws.get_all_values()
    if not all_rows:
        return
    by_symbol = {r["symbol"]: r for r in portfolio_rows}
    headers = ["Avg Buy", "Value", "Invested", "Shares", "P&L", "Return %",
               "Wt %", "Wt Status", "SL Price", "Target", "Buy More@",
               "Signal", "Risk Score", "Risk Level"]
    keys = ["avg_buy", "value", "invested", "shares", "pnl", "return_pct",
            "wt_pct", "wt_status", "sl_price", "target", "buy_more",
            "signal", "risk_score", "risk_level"]
    start_col = 3
    seen = set()
    data_rows = []
    for row in all_rows[1:]:
        sym = row[1].strip().upper() if len(row) > 1 else ""
        if not sym or sym in seen or sym not in by_symbol:
            data_rows.append([""] * len(headers))
            continue
        seen.add(sym)
        pr = by_symbol[sym]
        data_rows.append([pr.get(k, "") if pr.get(k) is not None else "" for k in keys])
    end_col = start_col + len(headers) - 1
    header_range = f"{gspread.utils.rowcol_to_a1(1, start_col)}:{gspread.utils.rowcol_to_a1(1, end_col)}"
    data_range = f"{gspread.utils.rowcol_to_a1(2, start_col)}:{gspread.utils.rowcol_to_a1(1+len(data_rows), end_col)}"
    existing_header = all_rows[0][start_col-1:start_col-1+len(headers)] if len(all_rows[0]) >= start_col else []
    if existing_header != headers:
        ws.update(header_range, [headers])
    if data_rows:
        ws.update(data_range, data_rows, value_input_option="RAW")
