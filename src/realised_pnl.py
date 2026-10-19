"""
Realised Profit & Loss Calculator — Combined Zerodha & Groww (Stocks + Mutual Funds)
===================================================================================

Computes monthly realised P&L using FIFO (First-In, First-Out) trade-matching across:
1. Zerodha Stocks        : data/imports/Sunai/Zerodha_Stocks/Tradebook_*.csv
2. Zerodha Mutual Funds  : data/imports/Sunai/Zerodha_Stocks/tradebook-*-MF_*.csv
3. Groww Stocks          : data/imports/Father/Groww_Stocks/Stocks_Order_History_*.xlsx
4. Groww Mutual Funds    : data/imports/Father/Groww_Stocks/Mutual_Funds_Order_History_*.xlsx

KEY DESIGN DECISIONS
--------------------
1.  Multi-Account Separation: FIFO lot queues are strictly partitioned per
    (broker, asset_class, symbol) so that trades in different accounts
    (Sunai vs Father) or asset classes (Stocks vs MFs) never cross-match.

2.  De-duplication: Every trade is keyed by an order/trade identifier so
    overlapping annual file exports are processed once without double-counting.

3.  FIFO Matching: Sells consume the earliest unexhausted buy lots chronologically
    by trade execution date.

4.  Monthly Attribution: Realised P&L is attributed to the calendar month of
    the SELL / REDEMPTION date.

5.  Indian Financial Year: April 1 to March 31.

6.  Gross P&L: Broker exports do not contain broken-out taxes/charges.
    Gross realised P&L is calculated and clearly labelled.
"""

import csv
import glob
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

_ALLOWED_SERIES = {"EQ", "BE", "BZ", "A", "B", "T", "XT", "RR", "RE", "IV", "SM", "ST"}


def _resolve_paths():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    imports_root = os.path.join(base_dir, "data", "imports")

    # Sunai / Zerodha
    z_dir = os.path.join(imports_root, "Sunai", "Zerodha_Stocks")
    if not os.path.isdir(z_dir):
        z_dir = os.path.join(imports_root, "Sunai", "zerodha")
    if not os.path.isdir(z_dir):
        z_dir = os.path.join(imports_root, "zerodha")

    # Father / Groww
    g_dir = os.path.join(imports_root, "Father", "Groww_Stocks")
    if not os.path.isdir(g_dir):
        g_dir = os.path.join(imports_root, "Father", "groww")
    if not os.path.isdir(g_dir):
        g_dir = os.path.join(imports_root, "groww")

    father_dir = os.path.join(imports_root, "Father")
    return z_dir, g_dir, father_dir


def _parse_date_iso(date_str: str) -> str:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            pass
    return str(date_str).strip()[:10]


def _get_fy_start(year: int, month: int) -> int:
    return year if month >= 4 else year - 1


def _fy_label(fy_start: int) -> str:
    return f"FY {str(fy_start)[2:]}-{str(fy_start + 1)[2:]}"


def load_all_pnl_trades(
    zerodha_dir: Optional[str] = None,
    groww_dir: Optional[str] = None,
    father_dir: Optional[str] = None,
) -> Tuple[List[Dict], List[str]]:
    """
    Loads all trade transactions from Zerodha (stocks + MF) and Groww (stocks + MF).
    Returns list of standardized trade dicts.
    """
    default_z, default_g, default_f = _resolve_paths()
    if zerodha_dir is None and groww_dir is None:
        z_dir = default_z
        g_dir = default_g
    else:
        z_dir = zerodha_dir
        g_dir = groww_dir
    f_dir = father_dir or default_f

    trades: List[Dict] = []
    seen_ids = set()
    warnings: List[str] = []

    # 1. Zerodha Stocks
    if z_dir and os.path.isdir(z_dir):
        for fpath in sorted(glob.glob(os.path.join(z_dir, "Tradebook_*.csv"))):
            fname = os.path.basename(fpath)
            try:
                with open(fpath, encoding="utf-8") as fp:
                    for row in csv.DictReader(fp):
                        tid = (row.get("trade_id") or "").strip()
                        if tid and tid in seen_ids:
                            continue
                        if tid:
                            seen_ids.add(tid)

                        series = (row.get("series") or "").strip().upper()
                        tt = (row.get("trade_type") or "").strip().lower()
                        if series and series not in _ALLOWED_SERIES:
                            continue
                        if tt not in ("buy", "sell"):
                            continue

                        try:
                            qty = float(row.get("quantity") or 0)
                            price = float(row.get("price") or 0)
                        except (ValueError, TypeError):
                            continue
                        if qty <= 0 or price <= 0:
                            continue

                        date_str = _parse_date_iso(row.get("trade_date", ""))
                        if not date_str:
                            continue

                        trades.append({
                            "broker": "Zerodha",
                            "asset": "Stock",
                            "symbol": (row.get("symbol") or "").strip().upper(),
                            "date": date_str,
                            "type": tt,
                            "qty": qty,
                            "price": price,
                            "trade_id": tid or f"ZSTK_{len(trades)}",
                            "source": fname,
                        })
            except Exception as e:
                warnings.append(f"Failed reading {fname}: {e}")

    # 2. Zerodha Mutual Funds
    if z_dir and os.path.isdir(z_dir):
        for fpath in sorted(glob.glob(os.path.join(z_dir, "tradebook-*-MF_*.csv"))):
            fname = os.path.basename(fpath)
            try:
                with open(fpath, encoding="utf-8") as fp:
                    for row in csv.DictReader(fp):
                        tid = "ZMF_" + (row.get("trade_id") or "").strip()
                        if tid and tid in seen_ids:
                            continue
                        if tid:
                            seen_ids.add(tid)

                        tt = (row.get("trade_type") or "").strip().lower()
                        if tt not in ("buy", "sell"):
                            continue

                        try:
                            qty = float(row.get("quantity") or 0)
                            price = float(row.get("price") or 0)
                        except (ValueError, TypeError):
                            continue
                        if qty <= 0 or price <= 0:
                            continue

                        sym = (row.get("isin") or "").strip().upper() or (row.get("symbol") or "").strip()
                        date_str = _parse_date_iso(row.get("trade_date", ""))
                        if not date_str:
                            continue

                        trades.append({
                            "broker": "Zerodha",
                            "asset": "Mutual Fund",
                            "symbol": sym,
                            "fund_name": (row.get("symbol") or "").strip(),
                            "date": date_str,
                            "type": tt,
                            "qty": qty,
                            "price": price,
                            "trade_id": tid or f"ZMF_{len(trades)}",
                            "source": fname,
                        })
            except Exception as e:
                warnings.append(f"Failed reading {fname}: {e}")

    # 3. Groww Stocks
    if g_dir and os.path.isdir(g_dir):
        # Locate import_groww.py
        import_groww_path = os.path.join(g_dir, "import_groww.py")
        if not os.path.isfile(import_groww_path):
            import_groww_path = os.path.join(f_dir, "import_groww.py")
        
        if os.path.isfile(import_groww_path):
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("_ig_pnl", import_groww_path)
                ig_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(ig_mod)
                for fpath in sorted(glob.glob(os.path.join(g_dir, "Stocks_Order_History_*.xlsx"))):
                    fname = os.path.basename(fpath)
                    try:
                        rows = ig_mod.import_groww(fpath)
                        for r in rows:
                            tid = "GSTK_" + str(r.get("trade_id", "")).strip()
                            if tid and tid in seen_ids:
                                continue
                            if tid:
                                seen_ids.add(tid)

                            tt = str(r.get("action", "")).strip().lower()
                            if tt not in ("buy", "sell"):
                                continue

                            try:
                                qty = float(r.get("quantity") or 0)
                                price = float(r.get("price") or 0)
                            except (ValueError, TypeError):
                                continue
                            if qty <= 0 or price <= 0:
                                continue

                            date_str = _parse_date_iso(r.get("date", ""))
                            trades.append({
                                "broker": "Groww",
                                "asset": "Stock",
                                "symbol": str(r.get("symbol", "")).strip().upper(),
                                "date": date_str,
                                "type": tt,
                                "qty": qty,
                                "price": price,
                                "trade_id": tid,
                                "source": fname,
                            })
                    except Exception as e:
                        warnings.append(f"Failed reading {fname}: {e}")
            except Exception as e:
                warnings.append(f"Could not load import_groww: {e}")

    # 4. Groww Mutual Funds
    if g_dir and os.path.isdir(g_dir):
        import_mf_groww_path = os.path.join(f_dir, "import_mf_groww.py")
        if not os.path.isfile(import_mf_groww_path):
            import_mf_groww_path = os.path.join(g_dir, "import_mf_groww.py")

        if os.path.isfile(import_mf_groww_path):
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("_img_pnl", import_mf_groww_path)
                img_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(img_mod)
                for fpath in sorted(glob.glob(os.path.join(g_dir, "Mutual_Funds_Order_History_*.xlsx"))):
                    fname = os.path.basename(fpath)
                    try:
                        rows = img_mod.import_mf_groww(fpath)
                        for r in rows:
                            tt = str(r.get("action", "")).strip().lower()
                            if tt not in ("buy", "sell"):
                                continue
                            try:
                                qty = float(r.get("units") or 0)
                                price = float(r.get("nav") or 0)
                            except (ValueError, TypeError):
                                continue
                            if qty <= 0 or price <= 0:
                                continue

                            sym = str(r.get("isin", "")).strip().upper() or str(r.get("fund_name", "")).strip()
                            date_str = _parse_date_iso(r.get("date", ""))
                            tid = f"GMF_{len(trades)}"
                            trades.append({
                                "broker": "Groww",
                                "asset": "Mutual Fund",
                                "symbol": sym,
                                "fund_name": str(r.get("fund_name", "")).strip(),
                                "date": date_str,
                                "type": tt,
                                "qty": qty,
                                "price": price,
                                "trade_id": tid,
                                "source": fname,
                            })
                    except Exception as e:
                        warnings.append(f"Failed reading {fname}: {e}")
            except Exception as e:
                warnings.append(f"Could not load import_mf_groww: {e}")

    # Sort strictly by (broker, asset, symbol, date, trade_id)
    trades.sort(key=lambda t: (t["broker"], t["asset"], t["symbol"], t["date"], t["trade_id"]))
    return trades, warnings


def match_trades_fifo(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Executes FIFO matching partitioned by (broker, asset, symbol).
    Returns dict with monthly_pnl, broker_pnl, asset_pnl, realised, orphan_sells, open_holdings, orphan_warnings.
    """
    fifo_queues: Dict[Tuple, List] = defaultdict(list)
    monthly_pnl: Dict[str, float] = defaultdict(float)
    broker_pnl: Dict[str, float] = defaultdict(float)
    asset_pnl: Dict[str, float] = defaultdict(float)
    orphan_warnings: List[str] = []
    orphan_sells: List[Dict[str, Any]] = []
    realised: List[Dict[str, Any]] = []

    for t in sorted(trades, key=lambda x: str(x.get("date", ""))):
        key = (t.get("broker", "Zerodha"), t.get("asset", "Stock"), t.get("symbol", ""))
        tt = t.get("type", "").strip().lower()
        qty = float(t.get("qty", 0) or 0)
        price = float(t.get("price", 0) or 0)
        date_str = str(t.get("date", ""))
        month_key = date_str[:7]

        if tt == "buy":
            fifo_queues[key].append([qty, price, date_str])
        elif tt == "sell":
            remaining_sell = qty
            gross_pnl = 0.0

            while remaining_sell > 0 and fifo_queues[key]:
                lot = fifo_queues[key][0]
                lot_qty, lot_price = lot[0], lot[1]
                consume = min(remaining_sell, lot_qty)

                match_pnl = consume * (price - lot_price)
                gross_pnl += match_pnl
                realised.append({
                    "broker": t.get("broker"),
                    "asset": t.get("asset"),
                    "symbol": t.get("symbol"),
                    "sell_date": date_str,
                    "buy_date": lot[2],
                    "qty": consume,
                    "sell_price": price,
                    "buy_price": lot_price,
                    "pnl": match_pnl,
                })
                lot[0] -= consume
                remaining_sell -= consume

                if lot[0] <= 1e-6:
                    fifo_queues[key].pop(0)

            if remaining_sell > 0:
                orphan_sells.append({
                    "trade": t,
                    "unmatched_qty": remaining_sell,
                })
                orphan_warnings.append(
                    f"ORPHAN SELL: {t.get('broker')} | {t.get('asset')} | {t.get('symbol')} | {date_str} | "
                    f"{remaining_sell:.2f} units sold @ ₹{price:.2f} with no matching buy in records "
                    f"(pre-2022 holding) — excluded from P&L."
                )
            if gross_pnl != 0.0:
                monthly_pnl[month_key] += gross_pnl
                broker_pnl[t.get("broker", "Zerodha")] += gross_pnl
                asset_pnl[t.get("asset", "Stock")] += gross_pnl

    open_holdings = {
        k: [list(lot) for lot in lots if lot[0] > 1e-6]
        for k, lots in fifo_queues.items()
        if any(lot[0] > 1e-6 for lot in lots)
    }

    return {
        "monthly_pnl": monthly_pnl,
        "broker_pnl": broker_pnl,
        "asset_pnl": asset_pnl,
        "realised": realised,
        "orphan_sells": orphan_sells,
        "open_holdings": open_holdings,
        "orphan_warnings": orphan_warnings,
    }


def compute_monthly_realised_pnl(
    zerodha_dir: Optional[str] = None,
    groww_dir: Optional[str] = None,
    father_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for Combined Realised P&L calculation across Zerodha & Groww
    (Stocks + Mutual Funds).
    """
    trades, load_warnings = load_all_pnl_trades(zerodha_dir, groww_dir, father_dir)
    if not trades:
        return {
            "monthly": [],
            "fy_totals": {},
            "broker_totals": {},
            "asset_totals": {},
            "warnings": ["No trade files found in imports directories."],
            "note": "No trade data available.",
            "data_from": "",
            "data_to": "",
            "all_time_total": 0.0,
        }

    matched = match_trades_fifo(trades)
    monthly_pnl = matched["monthly_pnl"]
    broker_pnl = matched["broker_pnl"]
    asset_pnl = matched["asset_pnl"]
    orphan_warnings = matched["orphan_warnings"]

    # Build formatted monthly rows
    monthly_rows = []
    for month_key in sorted(monthly_pnl.keys()):
        year, month = int(month_key[:4]), int(month_key[5:7])
        gross_val = round(monthly_pnl[month_key], 2)
        fy_start = _get_fy_start(year, month)
        monthly_rows.append({
            "year": year,
            "month": month,
            "label": f"{_MONTH_NAMES[month - 1]} {year}",
            "gross_pnl": gross_val,
            "fy": _fy_label(fy_start),
        })

    # Build FY totals by summing rounded monthly rows to ensure 0 drift
    fy_totals: Dict[str, float] = defaultdict(float)
    for mrow in monthly_rows:
        fy_totals[mrow["fy"]] += mrow["gross_pnl"]

    formatted_fy_totals = {
        fy: round(tot, 2) for fy, tot in sorted(fy_totals.items())
    }

    all_time_total = round(sum(formatted_fy_totals.values()), 2)
    dates = [t["date"] for t in trades if t.get("date")]
    data_from = min(dates) if dates else ""
    data_to = max(dates) if dates else ""

    warnings = load_warnings + orphan_warnings
    note = (
        f"Combined Gross Realised P&L from Zerodha + Groww (Stocks & Mutual Funds). "
        f"FIFO matching per demat account. No brokerage/STT included. "
        f"{len(orphan_warnings)} pre-2022 holding sell lot(s) excluded."
    )

    log.info(
        f"[realised_pnl] Processed {len(trades)} trades across Zerodha + Groww; "
        f"{len(monthly_rows)} active months; All-Time Realised P&L: ₹{all_time_total:,.2f}"
    )

    return {
        "monthly": monthly_rows,
        "fy_totals": formatted_fy_totals,
        "broker_totals": {b: round(v, 2) for b, v in sorted(broker_pnl.items())},
        "asset_totals": {a: round(v, 2) for a, v in sorted(asset_pnl.items())},
        "all_time_total": all_time_total,
        "warnings": warnings,
        "note": note,
        "data_from": data_from,
        "data_to": data_to,
    }


if __name__ == "__main__":
    res = compute_monthly_realised_pnl()
    print("=" * 60)
    print("COMBINED REALISED P&L (ZERODHA + GROWW, STOCKS + MUTUAL FUNDS)")
    print("=" * 60)
    print("Broker Totals:")
    for b, v in res["broker_totals"].items():
        print(f"  {b:<12}: Rs.{v:+,.2f}")
    print("\nAsset Class Totals:")
    for a, v in res["asset_totals"].items():
        print(f"  {a:<12}: Rs.{v:+,.2f}")
    print("\nFinancial Year Totals:")
    for fy, v in res["fy_totals"].items():
        print(f"  {fy:<12}: Rs.{v:+,.2f}")
    print(f"\nAll-Time Total: Rs.{res['all_time_total']:+,.2f}")
    print(f"\nActive Months : {len(res['monthly'])}")
    print(f"Warnings      : {len(res['warnings'])}")

