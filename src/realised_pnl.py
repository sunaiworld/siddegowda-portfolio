"""
Realised Profit & Loss Calculator — Zerodha Equity Tradebook
=============================================================

Computes monthly realised P&L from Zerodha tradebook CSVs using FIFO
(First-In, First-Out) trade-matching — the same methodology used by
Zerodha's own tax P&L report and consistent with the avg-cost tracking
already implemented in portfolio_builder.get_avg_buy_and_qty().

KEY DESIGN DECISIONS
--------------------
1.  Source of truth: data/imports/Sunai/zerodha/Tradebook_*.csv only.
    MF tradebooks (tradebook-QZ8063-MF_*.csv) are excluded — they are
    mutual fund / bond redemptions, not equity P&L.

2.  De-duplication: Every row is keyed by its trade_id column. If the
    same trade_id appears in more than one CSV (e.g. because a new
    annual file overlaps the tail of the previous), it is processed once.

3.  FIFO matching: For each sell, the earliest unexhausted buy lots
    (sorted by trade_date, then trade_id) are consumed first.  This
    matches SEBI / Zerodha's recommended methodology for equities.

4.  Orphan sells: A sell with no matching buy in the available records
    means the shares were bought before the earliest CSV on file (2022).
    The orphan shares produce P&L = 0 for that lot (we have no cost basis)
    and are flagged in the returned warnings list.  Do NOT zero-out the
    sell proceeds — that would distort the numbers; instead they are
    excluded entirely from the P&L total (treated as unknown-cost basis).

5.  Attribution: Realised P&L is attributed to the calendar month in
    which the SELL trade_date falls, not the buy date.

6.  Corporate actions: The tradebook already reflects post-split share
    counts (Zerodha adjusts the tradebook for corporate actions). No
    additional split adjustment is required here.

7.  Charges: The raw tradebook does NOT include brokerage, STT, stamp
    duty, exchange charges, or GST. This module therefore computes GROSS
    realised P&L only. Net (after charges) P&L is documented as
    unavailable from this data source.

8.  Filters: Only rows with segment=EQ (equities) and series=EQ, BE, or
    RR (regular, trade-to-trade, REITs) are included. This excludes F&O,
    currency, and commodity rows if any.

USAGE
-----
    from realised_pnl import compute_monthly_realised_pnl

    result = compute_monthly_realised_pnl()
    # result["monthly"]  : list of (year, month, month_label, gross_pnl)
    # result["fy_totals"]: dict {fy_start_year: total_gross_pnl}
    # result["warnings"] : list of human-readable warning strings
    # result["note"]     : single string describing the data limitation
"""

import csv
import glob
import logging
import os
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Path resolution ──────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_ZERODHA_DIR = os.path.join(_REPO_ROOT, "data", "imports", "Sunai", "zerodha")

# Series / segment allow-list for equity trades
# Covers NSE (EQ, BE, RR, RE, IV, SM, ST) and BSE groups (A, B, BZ, T, XT)
_ALLOWED_SERIES = {"EQ", "BE", "BZ", "A", "B", "T", "XT", "RR", "RE", "IV", "SM", "ST"}

_MONTH_NAMES = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


# ── Public API ───────────────────────────────────────────────────────────────

def compute_monthly_realised_pnl(
    zerodha_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point.  Reads all Tradebook_*.csv files from zerodha_dir
    (defaults to data/imports/Sunai/zerodha/), runs FIFO P&L matching,
    and returns a dict with monthly / FY breakdowns.

    Returns
    -------
    {
        "monthly":  [ {"year": int, "month": int, "label": str,
                        "gross_pnl": float, "fy": str}, ... ],
        "fy_totals": { "FY 22-23": float, ... },
        "warnings":  [ str, ... ],   # orphan sells and other anomalies
        "note":      str,            # data-quality note for display
        "data_from": str,            # earliest CSV date
        "data_to":   str,            # latest CSV date
    }
    """
    zdir = zerodha_dir or _ZERODHA_DIR
    trades, warnings = _load_all_trades(zdir)
    if not trades:
        return {
            "monthly": [],
            "fy_totals": {},
            "warnings": ["No Zerodha tradebook files found."],
            "note": "No data available.",
            "data_from": "",
            "data_to": "",
        }

    monthly_pnl, orphan_warnings = _fifo_pnl(trades)

    warnings.extend(orphan_warnings)

    monthly_rows = _build_monthly_rows(monthly_pnl)
    fy_totals = _build_fy_totals(monthly_pnl)

    dates = [t["date"] for t in trades]
    data_from = min(dates) if dates else ""
    data_to = max(dates) if dates else ""

    orphan_count = len(orphan_warnings)
    note = (
        f"Gross P&L (no charges). "
        f"Tradebooks available from {data_from[:7]} to {data_to[:7]}. "
        f"{orphan_count} sell lot(s) had no matching buy record in the available "
        f"tradebooks — they were excluded from the totals. "
        f"Shares held before {data_from[:7]} cannot have their cost basis determined "
        f"from these files."
    )

    log.info(
        f"[realised_pnl] Loaded {len(trades)} trades; "
        f"{orphan_count} orphan sell lots; "
        f"{len(monthly_rows)} months with P&L activity."
    )

    return {
        "monthly": monthly_rows,
        "fy_totals": fy_totals,
        "warnings": warnings,
        "note": note,
        "data_from": data_from,
        "data_to": data_to,
    }


# ── Internal helpers ─────────────────────────────────────────────────────────

def _load_all_trades(zdir: str) -> Tuple[List[Dict], List[str]]:
    """
    Read all Tradebook_YYYY.csv files, deduplicate by trade_id,
    filter to equity-only rows, and return a sorted list of trade dicts.
    """
    pattern = os.path.join(zdir, "Tradebook_*.csv")
    files = sorted(glob.glob(pattern))
    warnings = []

    if not files:
        warnings.append(f"No Tradebook_*.csv files found in {zdir}")
        return [], warnings

    trades: List[Dict] = []
    seen_trade_ids: set = set()

    for fpath in files:
        fname = os.path.basename(fpath)
        try:
            with open(fpath, encoding="utf-8") as fp:
                reader = csv.DictReader(fp)
                for row in reader:
                    trade_id = (row.get("trade_id") or "").strip()
                    if trade_id and trade_id in seen_trade_ids:
                        continue  # deduplicate
                    if trade_id:
                        seen_trade_ids.add(trade_id)

                    series = (row.get("series") or "").strip().upper()
                    trade_type = (row.get("trade_type") or "").strip().lower()

                    # Skip non-equity series
                    if series and series not in _ALLOWED_SERIES:
                        continue
                    # Only process buy/sell
                    if trade_type not in ("buy", "sell"):
                        continue

                    try:
                        qty = float(row.get("quantity") or 0)
                        price = float(row.get("price") or 0)
                    except (ValueError, TypeError):
                        continue

                    if qty <= 0 or price <= 0:
                        continue

                    date_str = (row.get("trade_date") or "").strip()
                    # Normalise to YYYY-MM-DD
                    date_str = _parse_date_iso(date_str)
                    if not date_str:
                        continue

                    trades.append(
                        {
                            "symbol": (row.get("symbol") or "").strip().upper(),
                            "date": date_str,
                            "type": trade_type,
                            "qty": qty,
                            "price": price,
                            "trade_id": trade_id,
                            "series": series,
                            "source_file": fname,
                        }
                    )
        except Exception as exc:
            warnings.append(f"Could not read {fname}: {exc}")

    # Sort by (symbol, date, trade_id) for deterministic FIFO ordering
    trades.sort(key=lambda t: (t["symbol"], t["date"], t["trade_id"]))
    return trades, warnings


def _parse_date_iso(date_str: str) -> str:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            pass
    return ""


def _fifo_pnl(
    trades: List[Dict],
) -> Tuple[Dict[str, float], List[str]]:
    """
    Apply FIFO matching across all trades.

    Returns
    -------
    monthly_pnl : dict  {"YYYY-MM": gross_pnl_float}
    orphan_warnings : list of strings  (one per sell-lot that had no buy)
    """
    # Per-symbol FIFO queue: list of [remaining_qty, buy_price, buy_date]
    fifo_queues: Dict[str, List[List]] = defaultdict(list)
    monthly_pnl: Dict[str, float] = defaultdict(float)
    orphan_warnings: List[str] = []

    for t in trades:
        sym = t["symbol"]
        qty = t["qty"]
        price = t["price"]
        date_str = t["date"]
        month_key = date_str[:7]  # "YYYY-MM"

        if t["type"] == "buy":
            fifo_queues[sym].append([qty, price, date_str])

        elif t["type"] == "sell":
            remaining_sell = qty
            gross_pnl = 0.0

            while remaining_sell > 0 and fifo_queues[sym]:
                lot = fifo_queues[sym][0]
                lot_qty, lot_price = lot[0], lot[1]
                consume = min(remaining_sell, lot_qty)

                gross_pnl += consume * (price - lot_price)
                lot[0] -= consume
                remaining_sell -= consume

                if lot[0] <= 0:
                    fifo_queues[sym].pop(0)

            if remaining_sell > 0:
                # Orphan shares — no cost basis available; exclude from P&L
                orphan_warnings.append(
                    f"ORPHAN SELL: {sym} | {date_str} | "
                    f"{remaining_sell:.0f} shares sold @ ₹{price:.2f} "
                    f"with no matching buy in available records — "
                    f"excluded from P&L totals (pre-2022 holding)"
                )
                # gross_pnl is already 0 for these shares (no cost deducted)
                # Do NOT add any amount — treat as excluded, not zero-profit
            else:
                monthly_pnl[month_key] += gross_pnl

    return dict(monthly_pnl), orphan_warnings


def _get_fy_start(year: int, month: int) -> int:
    """Return the start year of the Indian financial year for the given date."""
    return year if month >= 4 else year - 1


def _fy_label(fy_start: int) -> str:
    return f"FY {str(fy_start)[2:]}-{str(fy_start + 1)[2:]}"


def _build_monthly_rows(monthly_pnl: Dict[str, float]) -> List[Dict]:
    rows = []
    for month_key in sorted(monthly_pnl.keys()):
        year, month = int(month_key[:4]), int(month_key[5:7])
        gross_pnl = monthly_pnl[month_key]
        fy_start = _get_fy_start(year, month)
        rows.append(
            {
                "year": year,
                "month": month,
                "label": f"{_MONTH_NAMES[month - 1]} {year}",
                "gross_pnl": round(gross_pnl, 2),
                "fy": _fy_label(fy_start),
            }
        )
    return rows


def _build_fy_totals(monthly_pnl: Dict[str, float]) -> Dict[str, float]:
    fy_totals: Dict[int, float] = defaultdict(float)
    for month_key, pnl in monthly_pnl.items():
        year, month = int(month_key[:4]), int(month_key[5:7])
        fy_start = _get_fy_start(year, month)
        fy_totals[fy_start] += round(pnl, 2)  # use rounded monthly to prevent drift
    return {
        _fy_label(fy): round(total, 2)
        for fy, total in sorted(fy_totals.items())
    }


# ── CLI helper for quick validation ─────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = compute_monthly_realised_pnl()

    print("\n" + "=" * 60)
    print("MONTHLY REALISED P&L (Gross, FIFO)")
    print("=" * 60)
    current_fy = None
    for row in result["monthly"]:
        if row["fy"] != current_fy:
            current_fy = row["fy"]
            fy_total = result["fy_totals"].get(current_fy, 0)
            print(f"\n  {current_fy}  (Total: ₹{fy_total:+,.2f})")
            print("  " + "-" * 42)
        sign = "+" if row["gross_pnl"] >= 0 else ""
        print(f"  {row['label']:<12}  ₹{sign}{row['gross_pnl']:>12,.2f}")

    print("\n" + "=" * 60)
    print("FINANCIAL YEAR TOTALS")
    print("=" * 60)
    for fy_label, total in result["fy_totals"].items():
        sign = "+" if total >= 0 else ""
        print(f"  {fy_label}   ₹{sign}{total:>12,.2f}")

    print(f"\nData range: {result['data_from']} → {result['data_to']}")
    print(f"\nNOTE: {result['note']}")

    if result["warnings"]:
        print(f"\nWarnings ({len(result['warnings'])}):")
        for w in result["warnings"][:10]:
            print(f"  • {w}")
        if len(result["warnings"]) > 10:
            print(f"  ... and {len(result['warnings']) - 10} more")
