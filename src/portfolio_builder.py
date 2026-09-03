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
        all_rows = pws.get_all_values()
        if not all_rows:
            return symbols
            
        headers = all_rows[0]
        actual_sym_col = SYMBOL_COL
        for i, h in enumerate(headers):
            if str(h).strip().upper() == "SYMBOL":
                actual_sym_col = i
                break
                
        for row in all_rows[1:]:
            sym = row[actual_sym_col].strip().upper() if len(row) > actual_sym_col else ""
            if (sym and sym not in symbols and sym not in skip
                    and len(sym) <= 15 and sym.replace("&","").isalnum()):
                symbols.append(sym)
        log.info(f"Portfolio tab found {len(symbols)} symbols in col index {actual_sym_col}")
    except Exception as e:
        log.warning(f"Could not read Portfolio: {e}")
    return symbols

def read_portfolio_sources(sh):
    source_map = {}
    try:
        pws = sh.worksheet("Portfolio")
        all_rows = pws.get_all_values()
        if not all_rows: return source_map
        headers = all_rows[0]
        sym_col = -1
        src_col = -1
        for i, h in enumerate(headers):
            h_upper = str(h).strip().upper()
            if h_upper == "SYMBOL": sym_col = i
            if h_upper == "INVESTMENT SOURCE": src_col = i
        
        if sym_col >= 0 and src_col >= 0:
            for row in all_rows[1:]:
                if len(row) > max(sym_col, src_col):
                    sym = row[sym_col].strip().upper()
                    src = row[src_col].strip().upper()
                    if sym and src:
                        source_map[sym] = src
    except Exception as e:
        log.warning(f"Could not read source_map: {e}")
    return source_map


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
            t.get("import_source", ""),
            t.get("notes", ""),
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

def build_portfolio(prices, imports_dir="data/imports", fund_map=None, source_map=None, tech_map=None):
    if fund_map is None: fund_map = {}
    if source_map is None: source_map = {}
    if tech_map is None: tech_map = {}
    trades = load_all_trades(imports_dir)
    holdings = compute_holdings(trades)

    # Compute combined natively
    combined_dict = {}
    smallcase_syms = set()
    for t in trades:
        if "smallcase" in str(t.get("import_source", "")).lower() or "smallcase" in str(t.get("notes", "")).lower():
            smallcase_syms.add(str(t.get("symbol", "")).strip().upper())

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
        
        # Momentum returns
        t = tech_map.get(sym) or {}
        c["day_chg_pct"] = t.get("day_chg_pct", "")
        c["return_1w"] = t.get("return_1w", "")
        c["return_1m"] = t.get("return_1m", "")
        c["return_3m"] = t.get("return_3m", "")
        c["return_6m"] = t.get("return_6m", "")
        
        if sym in source_map and source_map[sym]:
            c["investment_source"] = source_map[sym].upper()
        elif fund_map.get(sym, {}).get("sector") == "ETFs" or "BEES" in sym.upper() or sym.upper().endswith("ETF") or sym.upper() in ("ICICIB22", "CPSEETF", "SETFNIF50", "GOLDBEES", "NIFTYBEES"):
            c["investment_source"] = "ETF"
        elif sym in smallcase_syms:
            c["investment_source"] = "SMALLCASE"
        elif c["invested"] > 0:
            c["investment_source"] = "SELF"
        else:
            c["investment_source"] = "UNKNOWN"

        if c["investment_source"] == "ETF":
            # ETFs represent broad basket/index allocation — exempt from generic -7% stock stop-loss
            c["sl_price"] = ""
            c["target"] = ""
            c["buy_more"] = round(c["avg_buy"] * 0.90, 2)
            if len(c["isins"]) > 1:
                c["signal"] = "REQUIRES REVIEW (Corp Action)"
            elif c["cmp"] <= c["buy_more"]:
                c["signal"] = "BUY MORE"
            else:
                c["signal"] = "HOLD"
        else:
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
    
    headers = PORTFOLIO_COLUMNS
    col_keys = {
        "Investment Source": "investment_source",
        "Shares": "shares", "Avg Buy": "avg_buy", "CMP": "cmp",
        "Day Chg%": "day_chg_pct", "1W Return %": "return_1w",
        "1M Return %": "return_1m", "3M Return %": "return_3m", "6M Return %": "return_6m",
        "Invested": "invested", "Value": "value", "P&L": "pnl",
        "Return %": "return_pct", "Wt %": "wt_pct",
        "Stop Loss": "sl_price", "Target": "target", "Buy More@": "buy_more", "Signal": "signal"
    }

    all_data = [headers]
    header_indices = []
    subtotal_indices = []

    combined_rows = portfolio_dict.get("combined", [])

    header_indices.append(len(all_data))
    group_row = [""] * len(headers)
    group_row[SYMBOL_COL] = "COMBINED - PORTFOLIO VIEW ONLY"
    all_data.append(group_row)
    
    if combined_rows:
        for r in combined_rows:
            sym = r["symbol"]
            row_data = [""] * len(headers)
            row_data[SYMBOL_COL] = sym
            for col_name, key in col_keys.items():
                if col_name in headers:
                    row_data[headers.index(col_name)] = r.get(key, "")
            all_data.append(row_data)
            
    subtotal_indices.append(len(all_data))
    tot_inv = sum(r["invested"] for r in combined_rows)
    tot_val = sum(r["value"] for r in combined_rows)
    tot_pnl = round(tot_val - tot_inv, 2)
    tot_ret = round((tot_pnl / tot_inv) * 100, 2) if tot_inv else 0
    
    subtotal_row = [""] * len(headers)
    subtotal_row[SYMBOL_COL] = "COMBINED TOTAL"
    if "Invested" in headers: subtotal_row[headers.index("Invested")] = round(tot_inv, 2)
    if "Value" in headers: subtotal_row[headers.index("Value")] = round(tot_val, 2)
    if "P&L" in headers: subtotal_row[headers.index("P&L")] = tot_pnl
    if "Return %" in headers: subtotal_row[headers.index("Return %")] = tot_ret
    all_data.append(subtotal_row)

    sheet_writer.clear_sheet_safe(ws)

    sheet_writer.update_sheet_safe(ws, "A1", all_data, value_input_option="RAW")
    log.info(f"write_portfolio: wrote {len(all_data)} rows to '{tab_name}'")
    profiler.increment("Rows written", len(all_data))

    nc = len(headers)
    width_map = {
        "Symbol": 70, "Investment Source": 120, "Shares": 55, "Avg Buy": 70, "CMP": 65,
        "Day Chg%": 55, "1W Return %": 65, "1M Return %": 65, "3M Return %": 65, "6M Return %": 65,
        "Invested": 85, "Value": 85, "P&L": 80, "Return %": 65, "Wt %": 55,
        "Stop Loss": 70, "Target": 70, "Buy More@": 70, "Signal": 100
    }
    widths = [width_map.get(h, 70) for h in headers]
    
    reqs = sheet_formatter.clear_all_formatting_reqs(ws.id) + sheet_formatter.get_structural_format_reqs(
        ws.id, len(all_data), nc, widths=widths, freeze_rows=1, freeze_cols=1)

    currency_cols = ["Avg Buy", "CMP", "Invested", "Value", "P&L", "Stop Loss", "Target", "Buy More@"]
    for col_name in currency_cols:
        if col_name in headers:
            col_idx = headers.index(col_name)
            reqs += sheet_formatter.get_currency_format_reqs(ws.id, 1, len(all_data), col_idx, col_idx + 1)

    pct_cols = ["Day Chg%", "1W Return %", "1M Return %", "3M Return %", "6M Return %", "Return %", "Wt %"]
    for col_name in pct_cols:
        if col_name in headers:
            col_idx = headers.index(col_name)
            reqs += sheet_formatter.get_percentage_format_reqs(ws.id, 1, len(all_data), col_idx, col_idx + 1)
            

    for i, row in enumerate(all_data):
        rn = i
        if i == 0 or len(row) <= SYMBOL_COL or row[SYMBOL_COL] == "" or "SUBTOTAL" in row[SYMBOL_COL] or "TOTAL" in row[SYMBOL_COL] or "GROWW" in row[SYMBOL_COL] or "ZERODHA" in row[SYMBOL_COL] or "COMBINED" in row[SYMBOL_COL]:
            continue

        if "Stop Loss" in headers:
            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, headers.index("Stop Loss"), "fde9d9", "c62828", bold=False))
        if "Target" in headers:
            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, headers.index("Target"), "d9ead3", "0b8043", bold=False))
        if "Buy More@" in headers:
            reqs.append(sheet_formatter.color_cell_req(ws.id, rn, headers.index("Buy More@"), "e8f0fe", "1967d2", bold=False))

        # Color Day Chg%, 1W Return %, 1M Return %, 3M Return %, 6M Return % with canonical return palette
        for ret_col in ("Day Chg%", "1W Return %", "1M Return %", "3M Return %", "6M Return %"):
            if ret_col in headers:
                c_idx = headers.index(ret_col)
                val_raw = row[c_idx] if c_idx < len(row) else ""
                try:
                    val_f = float(str(val_raw).replace("%", "").replace(",", "").strip())
                    if val_f > 0:
                        reqs.append(sheet_formatter.color_cell_req(ws.id, rn, c_idx, "d9ead3", "0b8043", bold=True))
                    elif val_f < 0:
                        reqs.append(sheet_formatter.color_cell_req(ws.id, rn, c_idx, "fde9d9", "c62828", bold=True))
                    else:
                        reqs.append(sheet_formatter.color_cell_req(ws.id, rn, c_idx, "f1f1f1", "666666", bold=False))
                except (ValueError, TypeError):
                    pass

    # Color the full width of section header and subtotal rows
    for h_idx in header_indices:
        for col in range(nc):
            reqs.append(sheet_formatter.color_cell_req(ws.id, h_idx, col, "0d1b2a", "ffffff", font_size=8))
            
    for s_idx in subtotal_indices:
        for col in range(nc):
            if col in [headers.index("P&L") if "P&L" in headers else -1, headers.index("Return %") if "Return %" in headers else -1]:
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
