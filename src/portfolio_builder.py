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


import csv
import glob


def read_trade_imports(imports_dir="data/imports"):
    trades = []
    for path in glob.glob(f"{imports_dir}/*.csv"):
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                continue
            field_map = {h.strip().lower(): h for h in reader.fieldnames}
            def find(*names):
                for n in names:
                    if n in field_map:
                        return field_map[n]
                return None
            f_sym    = find("symbol", "stock", "ticker")
            f_action = find("action", "type", "trade type")
            f_qty    = find("quantity", "qty")
            f_price  = find("price", "rate")
            f_date   = find("date")
            if not (f_sym and f_action and f_qty and f_price):
                continue
            for row in reader:
                try:
                    sym   = row.get(f_sym, "").strip().upper()
                    typ   = row.get(f_action, "").strip().upper()
                    qty   = float(row.get(f_qty, 0) or 0)
                    price = float(row.get(f_price, 0) or 0)
                    if not sym or typ not in ("BUY", "SELL") or qty <= 0:
                        continue
                    trades.append({
                        "symbol": sym, "action": typ,
                        "qty": qty, "price": price,
                        "date": row.get(f_date, "").strip() if f_date else "",
                    })
                except (TypeError, ValueError):
                    continue
    return trades


def compute_holdings(trades):
    book = {}
    for t in trades:
        sym = t["symbol"]
        cost, qty = book.get(sym, (0.0, 0.0))
        if t["action"] == "BUY":
            cost += t["qty"] * t["price"]
            qty  += t["qty"]
        elif t["action"] == "SELL" and qty > 0:
            avg = cost / qty
            cost -= t["qty"] * avg
            qty  -= t["qty"]
        book[sym] = (cost, max(qty, 0.0))

    holdings = {}
    for sym, (cost, qty) in book.items():
        if qty > 0:
            holdings[sym] = (round(cost / qty, 2), qty)
    return holdings


def build_portfolio(prices, imports_dir="data/imports"):
    trades = read_trade_imports(imports_dir)
    holdings = compute_holdings(trades)

    portfolio_live_value = sum(qty * prices.get(sym, 0) for sym, (_, qty) in holdings.items() if prices.get(sym))

    rows = []
    for sym, (avg_buy, qty) in holdings.items():
        cmp = prices.get(sym)
        if not cmp or cmp <= 0:
            continue

        invested = round(avg_buy * qty, 2)
        value    = round(cmp * qty, 2)
        pnl      = round(value - invested, 2)
        ret_pct  = round((pnl / invested) * 100, 2) if invested else 0
        wt_pct   = round((value / portfolio_live_value) * 100, 2) if portfolio_live_value else 0
        wt_status = "Underweight" if wt_pct < 2 else "Normal" if wt_pct <= 6 else "Overweight"

        sl_price = round(avg_buy * (1 - SL_PCT), 2)
        target   = round(avg_buy * (1 + TARGET_PCT), 2)
        buy_more = round(avg_buy * 0.90, 2)

        if cmp <= sl_price:
            signal = "SELL - SL HIT"
        elif cmp >= target:
            signal = "TARGET HIT - TRIM"
        elif cmp <= buy_more:
            signal = "BUY MORE"
        else:
            signal = "HOLD"

        rows.append({
            "symbol": sym, "shares": qty, "avg_buy": avg_buy, "cmp": cmp,
            "invested": invested, "value": value, "pnl": pnl, "return_pct": ret_pct,
            "wt_pct": wt_pct, "wt_status": wt_status,
            "sl_price": sl_price, "target": target, "buy_more": buy_more,
            "signal": signal,
        })
    return rows


def write_portfolio(sh, portfolio_rows, tab_name="Portfolio"):
    ws = sh.worksheet(tab_name)
    all_rows = ws.get_all_values()
    log.info(f"[CHECKPOINT] write_portfolio: worksheet={ws.title!r}, sheet rows={len(all_rows)}")
    log.info(f"[CHECKPOINT] write_portfolio: received {len(portfolio_rows)} portfolio_rows")
    if portfolio_rows:
        log.info(f"[CHECKPOINT] write_portfolio: first row = {portfolio_rows[0]}")
    if not all_rows:
        return
    by_symbol = {r["symbol"]: r for r in portfolio_rows}
    log.info(f"[CHECKPOINT] write_portfolio: by_symbol keys (first 5) = {list(by_symbol.keys())[:5]}")
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
    log.info(f"[CHECKPOINT] write_portfolio: header_range={header_range}, data_range={data_range}")
    if data_rows:
        log.info(f"[CHECKPOINT] write_portfolio: first data row being written = {data_rows[0]}")
    if existing_header != headers:
        ws.update(header_range, [headers])
    if data_rows:
        ws.update(data_range, data_rows, value_input_option="RAW")
