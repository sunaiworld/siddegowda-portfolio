# imports/zerodha/import_zerodha.py
"""
Zerodha importer – converts a Zerodha CSV export into rows that exactly match the
approved master‑trade schema.
"""

import csv
import os
from datetime import datetime
from typing import List, Dict


# --------------------------------------------------------------------------- #
# Helper – safe conversion helpers
# --------------------------------------------------------------------------- #
def _to_int(value: str) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def _to_float(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_date(value: str) -> str:
    """
    Zerodha dates are already ISO (YYYY‑MM‑DD) or may be DD‑MM‑YYYY.
    Ensure we always output ISO.
    """
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    # fallback – return as‑is (will be caught later if invalid)
    return value.strip()


# --------------------------------------------------------------------------- #
# Core importer
# --------------------------------------------------------------------------- #
def import_zerodha(csv_path: str) -> List[Dict[str, str]]:
    """
    Reads a Zerodha CSV export and returns a list of dicts that follow the master
    schema.
    """
    rows: List[Dict[str, str]] = []
    broker_name = "Zerodha"
    filename = os.path.basename(csv_path)

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            # Required columns – guard against missing headers
            trade_id = raw.get("trade_id", "").strip()
            if not trade_id:
                continue  # skip malformed rows

            # Normalise fields
            quantity = _to_int(raw.get("quantity", "0"))
            price = _to_float(raw.get("price", "0"))
            gross_amount = quantity * price

            row = {
                "trade_id": trade_id,
                "date": _parse_date(raw.get("trade_date", "")),
                "symbol": raw.get("symbol", "").strip(),
                "exchange": raw.get("exchange", "").strip(),
                "segment": raw.get("segment", "").strip(),
                "action": raw.get("trade_type", "").strip(),
                "quantity": str(quantity),
                "price": f"{price:.4f}",
                "gross_amount": f"{gross_amount:.2f}",
                "brokerage": "",          # Zerodha does not provide
                "taxes_charges": "",      # Zerodha does not provide
                "net_amount": "",         # Zerodha does not provide
                "broker": broker_name,
                "broker_order_id": raw.get("order_id", "").strip(),
                "currency": "",           # not supplied
                "import_source": filename,
                "notes": ""               # free‑text placeholder
            }
            rows.append(row)

    return rows
