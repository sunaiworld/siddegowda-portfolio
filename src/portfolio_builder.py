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
        cost, qty = book.get(sym, (0.0, 0.0))
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
        book[sym] = (cost, max(qty, 0.0))

    holdings = {}
    for sym, (cost, qty) in book.items():
        if qty > 1e-6:
            holdings[sym] = (round(cost / qty, 2), round(qty, 4))
    return holdings


def build_portfolio(prices, imports_dir="data/imports"):
    trades = load_all_trades(imports_dir)
    holdings = compute_holdings(trades)

    portfolio_live_value = sum(
        qty * prices.get(sym, 0) for sym, (_, qty) in holdings.items() if prices.get(sym)
    )

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
    if not all_rows:
        return

    by_symbol = {r["symbol"]: r for r in portfolio_rows}
    headers = ["Shares", "Avg Buy", "CMP", "Invested", "Value", "P&L", "Return %",
               "Wt %", "Wt Status", "Stop Loss", "Target", "Buy More@", "Signal"]
    keys = ["shares", "avg_buy", "cmp", "invested", "value", "pnl", "return_pct",
            "wt_pct", "wt_status", "sl_price", "target", "buy_more", "signal"]
    start_col = 3

    # ── Auto-append rows for symbols that exist in computed holdings but are
    #    not yet present in the sheet's column B.  This happens automatically
    #    when a new symbol first appears in the broker import files.
    existing_syms = set()
    for row in all_rows[1:]:
        sym = row[1].strip().upper() if len(row) > 1 else ""
        if sym:
            existing_syms.add(sym)

    new_syms = [sym for sym in by_symbol if sym not in existing_syms]
    if new_syms:
        log.info(f"write_portfolio: appending {len(new_syms)} new symbol(s) from broker imports: {new_syms}")
        append_rows = [[sym, sym] + [""] * (len(all_rows[0]) - 2 if all_rows[0] else 13) for sym in new_syms]
        # Append just col A (blank) and col B (symbol) — the rest will be filled below
        ws.append_rows([[sym] for sym in new_syms], value_input_option="RAW",
                       table_range=f"B{len(all_rows) + 1}")
        # Re-fetch after appending so row indices are correct
        all_rows = ws.get_all_values()

    seen = set()
    data_rows = []
    for row in all_rows[1:]:
        sym = row[1].strip().upper() if len(row) > 1 else ""
        if not sym or sym in seen or sym not in by_symbol:
            data_rows.append([""] * len(headers))
            continue
        seen.add(sym)
        pr = by_symbol[sym]
        data_rows.append([pr.get(k, "") for k in keys])

    end_col = start_col + len(headers) - 1
    header_range = f"{gspread.utils.rowcol_to_a1(1, start_col)}:{gspread.utils.rowcol_to_a1(1, end_col)}"
    data_range = f"{gspread.utils.rowcol_to_a1(2, start_col)}:{gspread.utils.rowcol_to_a1(1+len(data_rows), end_col)}"

    ws.update(header_range, [headers])
    if data_rows:
        ws.update(data_range, data_rows, value_input_option="RAW")


    num_rows = len(data_rows)
    # The Portfolio sheet has headers up to end_col.
    # Col A (0), Col B (1) are pre-existing. We format the whole width (end_col).
    reqs = sheet_formatter.get_structural_format_reqs(ws.id, num_rows, end_col, widths=None, freeze_rows=1, freeze_cols=2)

    # 0-indexed columns for currency: Avg Buy (3), CMP (4), Invested (5), Value (6), P&L (7), Stop Loss (11), Target (12), Buy More@ (13)
    for col in [3, 4, 5, 6, 7, 11, 12, 13]:
        reqs += sheet_formatter.get_currency_format_reqs(ws.id, 1, num_rows + 1, col, col + 1)

    # 0-indexed columns for percentage: Return % (8), Wt % (9)
    for col in [8, 9]:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, 1, num_rows + 1, col, col + 1)

    # Per-row conditional formatting
    for i, row in enumerate(data_rows):
        rn = i + 1

        pnl = row[keys.index("pnl")]
        req_pnl = sheet_formatter.color_positive_negative(ws.id, rn, 7, pnl)
        if req_pnl: reqs.append(req_pnl)

        ret = row[keys.index("return_pct")]
        req_ret = sheet_formatter.color_positive_negative(ws.id, rn, 8, ret)
        if req_ret: reqs.append(req_ret)

        signal = str(row[keys.index("signal")]).strip()
        req_sig = sheet_formatter.color_action_signal(ws.id, rn, 14, signal)
        if req_sig: reqs.append(req_sig)

    if reqs:
        sheet_writer.batch_update_safe(sh, reqs)
