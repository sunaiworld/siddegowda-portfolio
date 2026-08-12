import os
import json
import time
import logging
import statistics
import requests
import math
import csv
import glob
import importlib.util
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
import sheet_formatter
import sheet_writer
import news_engine.news_cache as news_cache
from news_engine.sources import google_news_rss
from news_engine import classifier

log = logging.getLogger("portfolio")

from config import *
from profiler import profiler


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
#
# read_trades() below reads the legacy "Trade Log" sheet tab. That tab is
# no longer how trades get into this system — real trades live in
# data/imports/{zerodha,groww} and are loaded via load_all_trades()
# further down this file. read_trades() is kept only because other code
# may still reference it; nothing in the daily pipeline should call it
# for portfolio math anymore. trades_to_legacy_rows() bridges the two:
# it converts load_all_trades()'s master-schema dicts into the same
# [symbol, date, type, qty, price] row shape read_trades() used to
# produce, so get_avg_buy_and_qty()/get_xirr()/get_entry_date() (and
# everything in main.py that depends on them — the Dashboard holdings
# dict, SL/target alerts, per-symbol XIRR) keep working unchanged and
# now source from the real trade data instead of the empty Trade Log tab.
# ══════════════════════════════════════════════
def read_trades(sh):
    try:
        return sh.worksheet("Trade Log").get_all_values()[1:]
    except:
        return []


def trades_to_legacy_rows(trades):
    """Adapts load_all_trades()'s master-schema dicts (symbol/date/action/
    quantity/price/...) into the legacy [symbol, date, type, qty, price]
    row list that get_avg_buy_and_qty/get_xirr/get_entry_date expect."""
    rows = []
    for t in trades:
        rows.append([
            t.get("symbol", ""),
            t.get("date", ""),
            t.get("action", ""),
            t.get("quantity", ""),
            t.get("price", ""),
        ])
    return rows


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
                # Clamp sell to available qty — older broker exports may start
                # mid-stream (sells before tracked buys), which would otherwise
                # produce negative total_qty and corrupt subsequent cost basis.
                sell_qty = min(qty, total_qty)
                avg = total_cost / total_qty
                total_cost -= sell_qty * avg
                total_qty  -= sell_qty
        except:
            continue
    total_qty = max(total_qty, 0.0)
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
    cashflows, dates, running_qty = [], [], 0.0
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
                cashflows.append(-qty * price); dates.append(dt)
                running_qty += qty
            elif typ == "SELL":
                # Clamp sell to available qty — prevents negative terminal value
                # when exports start mid-stream with sells before tracked buys.
                sell_qty = min(qty, running_qty) if running_qty > 0 else 0.0
                if sell_qty > 0:
                    cashflows.append(sell_qty * price); dates.append(dt)
                running_qty = max(running_qty - qty, 0.0)
        except:
            continue
    if running_qty > 0 and current_price:
        cashflows.append(running_qty * current_price)
        dates.append(date.today())
    if len(cashflows) < 2: return None
    r = compute_xirr(cashflows, dates)
    return round(r * 100, 2) if r else None
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


# ══════════════════════════════════════════════
# TRADE IMPORTS (data/imports/zerodha/*.csv + data/imports/groww/*.xlsx)
#
# Reuses the broker-specific importers that already live next to the raw
# exports (data/imports/zerodha/import_zerodha.py, data/imports/groww/
# import_groww.py) instead of re-parsing the files with a second, ad-hoc
# CSV reader. Those importers were already fixed/verified against the real
# Zerodha CSV headers (trade_date/trade_type/quantity/price) and the real
# Groww XLSX layout (5 metadata rows, header on row 6) — this file no
# longer duplicates that parsing logic, it just calls it.
#
# They live outside the src/ package (under data/imports/<broker>/), so
# they're loaded by absolute file path rather than a normal import — this
# works regardless of the process's cwd or sys.path, the same way the old
# read_trade_imports() resolved its directory.
# ══════════════════════════════════════════════
def _load_broker_importer(module_name, relative_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mod_path = os.path.join(repo_root, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_imports_root(imports_dir):
    """Mirror the old read_trade_imports() cwd-independent path resolution:
    try the given path as-is, then relative to cwd, then relative to this
    file's directory and its parent (repo root)."""
    candidates = [
        imports_dir,
        os.path.join(os.getcwd(), imports_dir),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), imports_dir),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), imports_dir),
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "zerodha")) or os.path.isdir(os.path.join(c, "groww")):
            return c
    return imports_dir


def load_all_trades(imports_dir="data/imports"):
    """Loads every Zerodha CSV and Groww XLSX under data/imports/{zerodha,groww}
    via the existing broker-specific importers and returns the combined list
    of master-schema trade dicts (symbol/action/quantity/price/...)."""
    imports_root = _resolve_imports_root(imports_dir)
    zerodha_dir = os.path.join(imports_root, "zerodha")
    groww_dir   = os.path.join(imports_root, "groww")

    trades = []

    if os.path.isdir(zerodha_dir):
        zerodha_mod = _load_broker_importer(
            "_zerodha_importer", os.path.join(zerodha_dir, "import_zerodha.py")
        )
        for path in sorted(glob.glob(os.path.join(zerodha_dir, "*.csv"))):
            try:
                trades.extend(zerodha_mod.import_zerodha(path))
            except Exception as e:
                log.warning(f"  Zerodha import failed for {os.path.basename(path)}: {e}")

    if os.path.isdir(groww_dir):
        groww_mod = _load_broker_importer(
            "_groww_importer", os.path.join(groww_dir, "import_groww.py")
        )
        for path in sorted(glob.glob(os.path.join(groww_dir, "*.xlsx"))):
            try:
                trades.extend(groww_mod.import_groww(path))
            except Exception as e:
                log.warning(f"  Groww import failed for {os.path.basename(path)}: {e}")

    log.info(f"Loaded {len(trades)} raw trade rows from data/imports (Zerodha + Groww)")
    trades.sort(key=lambda t: str(t.get("date", "")))
    return trades


def compute_holdings(trades):
    book = {}
    for t in trades:
        sym = str(t.get("symbol", "")).strip().upper()
        broker = str(t.get("broker", "")).strip()
        if not sym:
            continue
        try:
            action = str(t.get("action", "")).strip().upper()
            t_qty   = float(t.get("quantity", 0) or 0)
            t_price = float(t.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if t_qty <= 0:
            continue
        key = f"{broker}:{sym}"
        cost, qty = book.get(key, (0.0, 0.0))
        if action == "BUY":
            cost += t_qty * t_price
            qty  += t_qty
        elif action == "SELL" and qty > 0:
            # Clamp sell to available qty — older broker exports may start
            # mid-stream (sells before tracked buys), which would otherwise
            # produce negative qty/cost and corrupt the running avg cost basis.
            sell_qty = min(t_qty, qty)
            avg = cost / qty
            cost -= sell_qty * avg
            qty  -= sell_qty
        book[key] = (cost, max(qty, 0.0))

    holdings = {}
    for key, (cost, qty) in book.items():
        if qty > 1e-6:
            parts = key.split(":", 1)
            broker = parts[0]
            sym = parts[1] if len(parts) > 1 else key
            
            # Return raw cost alongside avg_buy so callers can use the true
            # invested amount for P&L/Return% without re-rounding errors.
            holdings[key] = {
                "symbol": sym,
                "broker": broker,
                "avg_buy": round(cost / qty, 2),
                "qty": round(qty, 4),
                "cost": round(cost, 2)
            }
    return holdings


import math

def is_valid_price(p):
    return p is not None and isinstance(p, (int, float)) and not math.isnan(p) and p > 0

def build_portfolio(prices, imports_dir="data/imports"):
    trades = load_all_trades(imports_dir)
    holdings = compute_holdings(trades)

    portfolio_live_value_g = sum(
        h["qty"] * prices.get(h["symbol"], 0) for h in holdings.values() if is_valid_price(prices.get(h["symbol"])) and h["broker"].lower() == "groww"
    )
    portfolio_live_value_z = sum(
        h["qty"] * prices.get(h["symbol"], 0) for h in holdings.values() if is_valid_price(prices.get(h["symbol"])) and h["broker"].lower() == "zerodha"
    )

    rows = []
    for key, h in holdings.items():
        sym = h["symbol"]
        broker = h["broker"]
        avg_buy = h["avg_buy"]
        qty = h["qty"]
        invested_raw = h["cost"]

        cmp = prices.get(sym)
        if not is_valid_price(cmp):
            continue

        # Use the true total cost (not avg_buy * qty) so rounding of avg_buy
        # does not distort Invested, P&L, and Return %.
        invested = round(invested_raw, 2)
        value    = round(cmp * qty, 2)
        pnl      = round(value - invested, 2)
        ret_pct  = round((pnl / invested) * 100, 2) if invested else 0
        
        live_val = portfolio_live_value_g if broker.lower() == "groww" else portfolio_live_value_z
        wt_pct   = round((value / live_val) * 100, 2) if live_val else 0
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
            "key": key,
            "broker": broker,
            "symbol": sym, "shares": qty, "avg_buy": avg_buy, "cmp": cmp,
            "invested": invested, "value": value, "pnl": pnl, "return_pct": ret_pct,
            "wt_pct": wt_pct, "wt_status": wt_status,
            "sl_price": sl_price, "target": target, "buy_more": buy_more,
            "signal": signal,
        })
    # Separate by broker
    groww_rows = [r for r in rows if r["broker"].lower() == "groww"]
    zerodha_rows = [r for r in rows if r["broker"].lower() == "zerodha"]

    # Compute combined
    combined_dict = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in combined_dict:
            combined_dict[sym] = {
                "symbol": sym,
                "shares": 0,
                "invested": 0.0,
                "value": 0.0,
                "avg_buy": 0.0,
                "cmp": r["cmp"],
            }
        combined_dict[sym]["shares"] += r["shares"]
        combined_dict[sym]["invested"] += r["invested"]
        combined_dict[sym]["value"] += r["value"]

    combined_rows = []
    portfolio_live_value_c = sum(r["value"] for r in combined_dict.values())
    for sym, c in combined_dict.items():
        if c["shares"] <= 0: continue
        c["avg_buy"] = round(c["invested"] / c["shares"], 2)
        c["pnl"] = round(c["value"] - c["invested"], 2)
        c["return_pct"] = round((c["pnl"] / c["invested"]) * 100, 2) if c["invested"] else 0
        c["wt_pct"] = round((c["value"] / portfolio_live_value_c) * 100, 2) if portfolio_live_value_c else 0
        c["wt_status"] = "Underweight" if c["wt_pct"] < 2 else "Normal" if c["wt_pct"] <= 6 else "Overweight"
        
        c["sl_price"] = round(c["avg_buy"] * (1 - SL_PCT), 2)
        c["target"] = round(c["avg_buy"] * (1 + TARGET_PCT), 2)
        c["buy_more"] = round(c["avg_buy"] * 0.90, 2)

        if c["cmp"] <= c["sl_price"]:
            c["signal"] = "SELL - SL HIT"
        elif c["cmp"] >= c["target"]:
            c["signal"] = "TARGET HIT - TRIM"
        elif c["cmp"] <= c["buy_more"]:
            c["signal"] = "BUY MORE"
        else:
            c["signal"] = "HOLD"
            
        combined_rows.append(c)

    return {"groww": groww_rows, "zerodha": zerodha_rows, "combined": combined_rows}


def write_portfolio(sh, portfolio_dict, tab_name="Portfolio"):
    ws = sh.worksheet(tab_name)
    existing_rows = ws.get_all_values()
    
    # Extract names for symbols to preserve Column A (Company Name)
    sym_name_map = {}
    if existing_rows:
        for row in existing_rows[1:]:
            name = row[0].strip() if len(row) > 0 else ""
            sym = row[1].strip().upper() if len(row) > 1 else ""
            if sym and sym not in sym_name_map and "SUBTOTAL" not in name and "TOTAL" not in name and name != "COMBINED TOTAL":
                sym_name_map[sym] = name

    headers = ["Name", "Symbol", "Shares", "Avg Buy", "CMP", "Invested", "Value", "P&L", "Return %",
               "Wt %", "Wt Status", "Stop Loss", "Target", "Buy More@", "Signal"]
    keys = ["shares", "avg_buy", "cmp", "invested", "value", "pnl", "return_pct",
            "wt_pct", "wt_status", "sl_price", "target", "buy_more", "signal"]

    all_data = [headers]
    header_indices = []
    subtotal_indices = []

    def _add_section(title, rows_list, tot_inv, tot_val):
        header_indices.append(len(all_data) + 1)
        all_data.append([title] + [""] * (len(headers) - 1))
        
        for r in rows_list:
            sym = r["symbol"]
            name = sym_name_map.get(sym, sym)
            row_data = [name, sym] + [r.get(k, "") for k in keys]
            all_data.append(row_data)
            
        subtotal_indices.append(len(all_data) + 1)
        tpnl = round(tot_val - tot_inv, 2)
        tret = round((tpnl / tot_inv) * 100, 2) if tot_inv else 0
        all_data.append([
            f"{title} SUBTOTAL", "", "", "", "",
            round(tot_inv, 2), round(tot_val, 2), tpnl, tret,
            "", "", "", "", "", ""
        ])
        all_data.append([""] * len(headers))

    groww_rows = portfolio_dict.get("groww", [])
    zerodha_rows = portfolio_dict.get("zerodha", [])
    combined_rows = portfolio_dict.get("combined", [])

    if groww_rows:
        inv = sum(r["invested"] for r in groww_rows)
        val = sum(r["value"] for r in groww_rows)
        _add_section("GROWW - DAD", groww_rows, inv, val)

    if zerodha_rows:
        inv = sum(r["invested"] for r in zerodha_rows)
        val = sum(r["value"] for r in zerodha_rows)
        _add_section("ZERODHA - SELF", zerodha_rows, inv, val)

    header_indices.append(len(all_data) + 1)
    all_data.append(["COMBINED - PORTFOLIO VIEW ONLY"] + [""] * (len(headers) - 1))
    
    if combined_rows:
        for r in combined_rows:
            sym = r["symbol"]
            name = sym_name_map.get(sym, sym)
            row_data = [name, sym] + [r.get(k, "") for k in keys]
            all_data.append(row_data)
            
    subtotal_indices.append(len(all_data) + 1)
    tot_inv = sum(r["invested"] for r in combined_rows)
    tot_val = sum(r["value"] for r in combined_rows)
    tot_pnl = round(tot_val - tot_inv, 2)
    tot_ret = round((tot_pnl / tot_inv) * 100, 2) if tot_inv else 0
    all_data.append([
        "COMBINED TOTAL", "", "", "", "",
        round(tot_inv, 2), round(tot_val, 2), tot_pnl, tot_ret,
        "", "", "", "", "", ""
    ])

    ws.clear()
    ws.update("A1", all_data, value_input_option="RAW")
    log.info(f"write_portfolio: wrote {len(all_data)} rows to '{tab_name}'")
    profiler.increment("Rows written", len(all_data))

    nc = len(headers)
    # Compact column widths — GITHUB DATA comparable sizes
    # Name(120), Symbol(70), Shares(55), Avg Buy(70), CMP(65),
    # Invested(85), Value(85), P&L(80), Return%(65), Wt%(55),
    # Wt Status(70), Stop Loss(70), Target(70), Buy More@(70), Signal(100)
    widths = [120, 70, 55, 70, 65, 85, 85, 80, 65, 55, 70, 70, 70, 70, 100]
    reqs = sheet_formatter.get_structural_format_reqs(
        ws.id, len(all_data), nc, widths=widths, freeze_rows=1, freeze_cols=2)

    # cols 0-indexed: Avg Buy(3), CMP(4), Invested(5), Value(6), P&L(7), Stop Loss(11), Target(12), Buy More@(13)
    for col in [3, 4, 5, 6, 7, 11, 12, 13]:
        reqs += sheet_formatter.get_currency_format_reqs(ws.id, 1, len(all_data), col, col + 1)

    # Return %(8), Wt %(9)
    for col in [8, 9]:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, 1, len(all_data), col, col + 1)

    for i, row in enumerate(all_data):
        rn = i + 1
        if i == 0 or len(row) <= 1 or row[0] == "" or "SUBTOTAL" in row[0] or "TOTAL" in row[0] or "GROWW" in row[0] or "ZERODHA" in row[0] or "COMBINED" in row[0]:
            continue

        try:
            pnl = float(row[7]) if row[7] else 0.0
            req = sheet_formatter.color_positive_negative(ws.id, rn, 7, pnl)
            if req: reqs.append(req)
        except: pass
        
        try:
            ret_pct = float(row[8]) if row[8] else 0.0
            req = sheet_formatter.color_positive_negative(ws.id, rn, 8, ret_pct)
            if req: reqs.append(req)
        except: pass

        try:
            xirr_pct = float(row[10]) if row[10] else 0.0
            req = sheet_formatter.color_positive_negative(ws.id, rn, 10, xirr_pct)
            if req: reqs.append(req)
        except: pass
        
        # Wt Status (col 10): Overweight=red, Underweight=blue, Normal=green
        wt_status = str(row[10]).strip()
        if wt_status == "Overweight":
            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 10, "fde9d9", "c62828"))
        elif wt_status == "Underweight":
            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 10, "d9eaf7", "1565c0"))
        elif wt_status == "Normal":
            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 10, "d9ead3", "0b8043"))

        signal = str(row[14]).strip()
        req_sig = sheet_formatter.color_action_signal(ws.id, rn, 14, signal)
        if req_sig: reqs.append(req_sig)

    for h_idx in header_indices:
        reqs.append(sheet_formatter.color_cell_req(ws.id, h_idx, 0, "0d1b2a", "ffffff"))
    for s_idx in subtotal_indices:
        reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx, 0, "37474f", "ffffff"))

    # Filter over the full table
    reqs.append({
        "setBasicFilter": {
            "filter": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": len(all_data),
                    "startColumnIndex": 0,
                    "endColumnIndex": nc,
                }
            }
        }
    })

    if reqs:
        sheet_writer.batch_update_safe(sh, reqs)
