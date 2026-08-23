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

import portfolio_analytics
import sheet_formatter
import sheet_writer

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
        isin = str(t.get("isin", "")).strip().upper()
        if not isin:
            isin = sym
        if not isin:
            continue
        try:
            action = str(t.get("action", "")).strip().upper()
            t_qty   = float(t.get("quantity", 0) or 0)
            t_price = float(t.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if t_qty <= 0:
            continue
            
        key = isin
        cost, qty, existing_sym = book.get(key, (0.0, 0.0, ""))
        if action == "BUY":
            cost += t_qty * t_price
            qty  += t_qty
        elif action == "SELL" and qty > 0:
            sell_qty = min(t_qty, qty)
            avg = cost / qty
            cost -= sell_qty * avg
            qty  -= sell_qty
            
        book[key] = (cost, max(qty, 0.0), sym or existing_sym)

    holdings = {}
    for isin, (cost, qty, sym) in book.items():
        if qty > 1e-6:
            holdings[isin] = {
                "symbol": sym,
                "broker": "Combined",
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

    # Compute combined natively
    combined_dict = {}
    for key, h in holdings.items():
        sym = h["symbol"]
        qty = h["qty"]
        invested_raw = h["cost"]
        cmp = prices.get(sym)
        if not is_valid_price(cmp):
            continue

        invested = round(invested_raw, 2)
        value    = round(cmp * qty, 2)
        
        if sym not in combined_dict:
            combined_dict[sym] = {
                "symbol": sym,
                "shares": 0.0,
                "invested": 0.0,
                "value": 0.0,
                "cmp": cmp,
                "isins": set()
            }
        combined_dict[sym]["shares"] += qty
        combined_dict[sym]["invested"] += invested
        combined_dict[sym]["value"] += value
        combined_dict[sym]["isins"].add(key)

    combined_rows = []
    portfolio_live_value_c = sum(c["value"] for c in combined_dict.values())
    
    for sym, c in combined_dict.items():
        if c["shares"] <= 0: continue
        c["avg_buy"] = round(c["invested"] / c["shares"], 2)
        c["pnl"] = round(c["value"] - c["invested"], 2)
        c["return_pct"] = round((c["pnl"] / c["invested"]) * 100, 2) if c["invested"] else 0
        c["wt_pct"] = round((c["value"] / portfolio_live_value_c) * 100, 2) if portfolio_live_value_c else 0
        
        
        c["sl_price"] = round(c["avg_buy"] * (1 - SL_PCT), 2)
        c["target"] = round(c["avg_buy"] * (1 + TARGET_PCT), 2)
        c["buy_more"] = round(c["avg_buy"] * 0.90, 2)

        if len(c["isins"]) > 1:
            c["signal"] = "REQUIRES REVIEW (Corp Action)"
        elif c["cmp"] <= c["sl_price"]:
            c["signal"] = "SELL - SL HIT"
        elif c["cmp"] >= c["target"]:
            c["signal"] = "TARGET HIT - TRIM"
        elif c["cmp"] <= c["buy_more"]:
            c["signal"] = "BUY MORE"
        else:
            c["signal"] = "HOLD"
            
        combined_rows.append(c)

    # We return an empty list for groww and zerodha to avoid breaking unpacking downstream
    return {"groww": [], "zerodha": [], "combined": combined_rows}


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
               "Wt %", "Stop Loss", "Target", "Buy More@", "Signal"]
    keys = ["shares", "avg_buy", "cmp", "invested", "value", "pnl", "return_pct",
            "wt_pct", "sl_price", "target", "buy_more", "signal"]

    all_data = [headers]
    header_indices = []
    subtotal_indices = []



    combined_rows = portfolio_dict.get("combined", [])

    header_indices.append(len(all_data))
    all_data.append(["COMBINED - PORTFOLIO VIEW ONLY"] + [""] * (len(headers) - 1))
    
    if combined_rows:
        for r in combined_rows:
            sym = r["symbol"]
            name = sym_name_map.get(sym, sym)
            row_data = [name, sym] + [r.get(k, "") for k in keys]
            all_data.append(row_data)
            
    subtotal_indices.append(len(all_data))
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
    
    try:
        rules = sh.fetch_sheet_metadata({"includeGridData": False})
        sheet_meta = next((s for s in rules.get('sheets', []) if s.get('properties', {}).get('sheetId') == ws.id), None)
        if sheet_meta:
            cond_formats = sheet_meta.get("conditionalFormats", [])
            if cond_formats:
                clear_reqs = [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}} for _ in cond_formats]
                sheet_writer.batch_update_safe(sh, clear_reqs)
    except:
        pass

    ws.update("A1", all_data, value_input_option="RAW")
    log.info(f"write_portfolio: wrote {len(all_data)} rows to '{tab_name}'")
    profiler.increment("Rows written", len(all_data))

    nc = len(headers)
    # Compact column widths — GITHUB DATA comparable sizes
    # Name(120), Symbol(70), Shares(55), Avg Buy(70), CMP(65),
    # Invested(85), Value(85), P&L(80), Return%(65), Wt%(55),
    # Wt Status(70), Stop Loss(70), Target(70), Buy More@(70), Signal(100)
    widths = [120, 70, 55, 70, 65, 85, 85, 80, 65, 55, 70, 70, 70, 100]
    reqs = sheet_formatter.clear_all_formatting_reqs(ws.id) + sheet_formatter.get_structural_format_reqs(
        ws.id, len(all_data), nc, widths=widths, freeze_rows=1, freeze_cols=2)

    # cols 0-indexed: Avg Buy(3), CMP(4), Invested(5), Value(6), P&L(7), Stop Loss(11), Target(12), Buy More@(13)
    for col in [3, 4, 5, 6, 7, 10, 11, 12]:
        reqs += sheet_formatter.get_currency_format_reqs(ws.id, 1, len(all_data), col, col + 1)

    # Return %(8), Wt %(9)
    for col in [8, 9]:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, 1, len(all_data), col, col + 1)

    for i, row in enumerate(all_data):
        rn = i
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

        
        reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 10, "fde9d9", "c62828", bold=False))
        reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 11, "d9ead3", "0b8043", bold=False))
        reqs.append(sheet_formatter.color_cell_req(ws.id, rn, 12, "e8f0fe", "1967d2", bold=False))

        signal = str(row[13]).strip()
        req_sig = sheet_formatter.color_action_signal(ws.id, rn, 13, signal)
        if req_sig: reqs.append(req_sig)

    # Color the full width of section header and subtotal rows
    for h_idx in header_indices:
        for col in range(nc):
            reqs.append(sheet_formatter.color_cell_req(ws.id, h_idx, col, "0d1b2a", "ffffff", font_size=8))
    for s_idx in subtotal_indices:
        for col in range(nc):
            if col in [7, 8]:
                try:
                    val = float(all_data[s_idx][col]) if all_data[s_idx][col] else 0.0
                    bg = "d9ead3" if val > 0 else "fde9d9" if val < 0 else "f1f1f1"
                    fg = "0b8043" if val > 0 else "c62828" if val < 0 else "666666"
                    reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx, col, bg, fg, bold=True, font_size=8))
                    continue
                except: pass
            reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx, col, "1c3144", "ffffff", font_size=8))

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
