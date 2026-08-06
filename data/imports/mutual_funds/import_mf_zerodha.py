# imports/mutual_funds/import_mf_zerodha.py
"""
Zerodha Mutual Fund importer – converts a Zerodha MF CSV export (tradebook-*-MF_*.csv)
into rows that follow the canonical mutual-fund schema.

Expected CSV columns:
    symbol, isin, trade_date, exchange, segment, series, trade_type, auction,
    quantity, price, trade_id, order_id, order_execution_time
"""

import csv
import logging
import os
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper – safe conversion helpers
# --------------------------------------------------------------------------- #
def _to_float(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _parse_date(value: str) -> str:
    """
    Zerodha dates are already ISO (YYYY-MM-DD) or may be DD-MM-YYYY.
    Ensure we always output ISO.
    """
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    # fallback – return as-is (will be caught later if invalid)
    return value.strip()


def _normalize_fund_name(symbol: str) -> str:
    """
    Title-case the raw fund symbol string.
    Fund-name matching is done by ISIN, so no stripping of suffixes is performed.
    """
    return symbol.strip().title()


# --------------------------------------------------------------------------- #
# Core importer
# --------------------------------------------------------------------------- #
def import_mf_zerodha(filepath: str) -> List[Dict]:
    """
    Reads a Zerodha MF CSV export and returns a list of dicts that follow the
    canonical mutual-fund schema.

    Schema keys returned:
        fund_name, isin, date, action, units, nav, amount, broker, import_source
    """
    rows: List[Dict] = []
    filename = os.path.basename(filepath)

    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for lineno, raw in enumerate(reader, start=2):  # start=2: header is line 1
            # Required columns – guard against missing headers
            trade_id = raw.get("trade_id", "").strip()
            if not trade_id:
                logger.warning("Line %d in %s: missing trade_id, skipping.", lineno, filename)
                continue

            units = _to_float(raw.get("quantity", "0"))
            nav = _to_float(raw.get("price", "0"))

            # Skip rows with non-positive units or NAV
            if units <= 0:
                logger.warning(
                    "Line %d in %s: units=%s <= 0, skipping.", lineno, filename, units
                )
                continue
            if nav <= 0:
                logger.warning(
                    "Line %d in %s: nav=%s <= 0, skipping.", lineno, filename, nav
                )
                continue

            row = {
                "fund_name": _normalize_fund_name(raw.get("symbol", "")),
                "isin": raw.get("isin", "").strip(),
                "date": _parse_date(raw.get("trade_date", "")),
                "action": raw.get("trade_type", "").strip().lower(),
                "units": units,
                "nav": nav,
                "amount": round(units * nav, 4),
                "broker": "Zerodha",
                "import_source": filename,
            }
            rows.append(row)

    return rows
