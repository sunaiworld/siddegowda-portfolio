# imports/groww/import_groww.py
"""
Groww importer - converts a Groww "Stock Order History" XLSX export into
rows that exactly match the approved master-trade schema.

2026-07-21 fix: the real export is NOT a flat table from row 1. It has
5 metadata/title rows first:
    Row 1: Name, <account holder>
    Row 2: Unique Client Code, <code>
    Row 3: (blank)
    Row 4: Order history for stocks from <date> to <date>
    Row 5: (blank)
    Row 6: Stock name | Symbol | ISIN | Type | Quantity | Value | Exchange |
           Exchange Order Id | Execution date and time | Order status
The previous version of this importer read row 1 as the header and
looked for trade_id/price/trade_date/trade_type columns that don't
exist in this export at all -> every row failed the `if not trade_id`
guard and 0 trades were ever imported, silently. Rewritten against the
actual files committed under data/imports/groww/.
"""

import os
from datetime import datetime
from typing import List, Dict

import pandas as pd  # pandas is the easiest way to read Excel files


# --------------------------------------------------------------------------- #
# Helper conversion utilities
# --------------------------------------------------------------------------- #
def _to_int(value) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _to_float(value) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_datetime(value) -> str:
    """
    Groww's 'Execution date and time' column looks like
    '01-01-2026 10:05 AM'. Convert to ISO date (time isn't part of
    the master schema's `date` field).
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    value = str(value).strip()
    for fmt in ("%d-%m-%Y %I:%M %p", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return value  # fallback


# --------------------------------------------------------------------------- #
# Core importer
# --------------------------------------------------------------------------- #
def import_groww(xlsx_path: str) -> List[Dict[str, str]]:
    """
    Reads a Groww "Stock Order History" XLSX export and returns a list
    of dicts that follow the master schema.

    The real export has 5 metadata/title rows above the actual table
    (Name, Unique Client Code, blank, title, blank), so the header is
    on row 6 - `header=5` (0-indexed) tells pandas to skip them.
    """
    broker_name = "Groww"
    filename = os.path.basename(xlsx_path)

    df = pd.read_excel(xlsx_path, engine="openpyxl", header=5)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

    rows: List[Dict[str, str]] = []

    for _, raw in df.iterrows():
        # Groww doesn't issue its own trade_id; the exchange order id
        # is unique per fill, so it doubles as one.
        order_id = str(raw.get("exchange_order_id", "")).strip()
        if not order_id or order_id.lower() == "nan":
            continue  # skip blank / malformed rows

        status = str(raw.get("order_status", "")).strip().lower()
        if status and status != "executed":
            continue  # only completed fills count as holdings

        quantity = _to_int(raw.get("quantity", 0))
        gross_value = _to_float(raw.get("value", 0))
        price = round(gross_value / quantity, 4) if quantity else 0.0
        action = str(raw.get("type", "")).strip().lower()  # 'buy' / 'sell'

        row = {
            "trade_id": order_id,
            "date": _parse_datetime(raw.get("execution_date_and_time")),
            "symbol": str(raw.get("symbol", "")).strip().upper(), "isin": str(raw.get("isin", "")).strip(),
            "exchange": str(raw.get("exchange", "")).strip(),
            "segment": "EQ",                 # not provided; all rows are equity delivery
            "action": action,
            "quantity": str(quantity),
            "price": f"{price:.4f}",
            "gross_amount": f"{gross_value:.2f}",
            "brokerage": "",                  # not broken out in this export
            "taxes_charges": "",              # not broken out in this export
            "net_amount": f"{gross_value:.2f}",
            "broker": broker_name,
            "broker_order_id": order_id,
            "currency": "",                   # not supplied
            "import_source": filename,
            "notes": str(raw.get("stock_name", "")).strip(),
        }
        rows.append(row)

    return rows
