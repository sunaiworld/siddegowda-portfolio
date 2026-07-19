# imports/groww/import_groww.py
"""
Groww importer – converts a Groww XLSX export into rows that exactly match the
approved master‑trade schema.
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


def _parse_date(value) -> str:
    """
    Groww stores dates as datetime objects or strings like 'dd‑mm‑yyyy'.
    Convert to ISO.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    value = str(value).strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    return value  # fallback


# --------------------------------------------------------------------------- #
# Core importer
# --------------------------------------------------------------------------- #
def import_groww(xlsx_path: str) -> List[Dict[str, str]]:
    """
    Reads a Groww XLSX export and returns a list of dicts that follow the master
    schema.
    """
    broker_name = "Groww"
    filename = os.path.basename(xlsx_path)

    # Read the workbook – assume the first sheet contains the data
    df = pd.read_excel(xlsx_path, engine="openpyxl")

    # Normalise column names (case‑insensitive)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rows: List[Dict[str, str]] = []

    for _, raw in df.iterrows():
        trade_id = str(raw.get("trade_id", "")).strip()
        if not trade_id:
            continue  # skip rows without a trade identifier

        quantity = _to_int(raw.get("quantity", 0))
        price = _to_float(raw.get("price", 0))

        row = {
            "trade_id": trade_id,
            "date": _parse_date(raw.get("trade_date")),
            "symbol": str(raw.get("scrip_name", "")).strip(),
            "exchange": str(raw.get("exchange", "")).strip(),
            "segment": str(raw.get("segment", "")).strip(),
            "action": str(raw.get("trade_type", "")).strip(),
            "quantity": str(quantity),
            "price": f"{price:.4f}",
            "gross_amount": str(_to_float(raw.get("trade_value", 0))),
            "brokerage": str(_to_float(raw.get("brokerage", 0))),
            "taxes_charges": str(_to_float(raw.get("other_charges", 0))),
            "net_amount": str(_to_float(raw.get("net_trade_value", 0))),
            "broker": broker_name,
            "broker_order_id": str(raw.get("order_id", "")).strip(),
            "currency": "",                         # not supplied
            "import_source": filename,
            "notes": ""                              # free‑text placeholder
        }
        rows.append(row)

    return rows
