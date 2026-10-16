# mutual_funds/import_mf_groww.py
"""
Groww Mutual Fund importer - converts a Groww "Mutual Funds Order History"
XLSX export into rows matching the canonical MF trade schema.

File structure (confirmed):
    Rows 1-11 : header metadata (skip)
    Row 12    : column headers — Scheme Name | Transaction Type | Units |
                NAV | Amount | Date
    Row 13    : blank (skip)
    Row 14+   : actual data rows (some rows may be blank — skip those)

Transaction Type values:
    'PURCHASE'  -> action = 'buy'
    'REDEMPTION' -> action = 'sell'

Numeric columns (Units, NAV, Amount) are strings with possible commas,
e.g. '49,997.00'.  Strip commas before converting to float.

Date format: '08 Jul 2026'  ->  strptime('%d %b %Y')
"""

import logging
import os
from datetime import datetime
from typing import Dict, List

import openpyxl

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Helper conversion utilities
# --------------------------------------------------------------------------- #

def _to_float(value, label: str = "") -> float:
    """Convert a possibly comma-containing string to float.  Returns 0.0 on failure."""
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        if label:
            logger.warning("Could not convert %s value %r to float", label, value)
        return 0.0


def _parse_date(value) -> str:
    """
    Parse a Groww MF date string '08 Jul 2026' into 'YYYY-MM-DD'.
    Falls back to str(value) if parsing fails.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    raw = str(value).strip()
    try:
        return datetime.strptime(raw, "%d %b %Y").strftime("%Y-%m-%d")
    except Exception:
        logger.warning("Could not parse date %r", raw)
        return raw


def _parse_action(tx_type) -> str:
    """Map Groww transaction type to canonical 'buy' / 'sell'."""
    t = str(tx_type).strip().upper()
    if t == "PURCHASE":
        return "buy"
    if t == "REDEMPTION":
        return "sell"
    # Unknown type — pass through lower-cased so callers can still inspect it.
    logger.warning("Unknown MF transaction type %r — treating as-is", t)
    return t.lower()


# --------------------------------------------------------------------------- #
# Core importer
# --------------------------------------------------------------------------- #

def import_mf_groww(filepath: str) -> List[Dict]:
    """
    Reads a Groww "Mutual Funds Order History" XLSX export and returns a list
    of dicts that follow the canonical MF schema.

    Parameters
    ----------
    filepath : str
        Absolute or relative path to the .xlsx file.

    Returns
    -------
    list of dict
        Each dict has keys: fund_name, isin, date, action, units, nav,
        amount, broker, import_source.
    """
    filename = os.path.basename(filepath)
    rows: List[Dict] = []

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active

    # Collect all rows as lists of cell values (openpyxl rows are tuples of cells)
    all_rows = list(ws.iter_rows(values_only=True))

    # Row 12 (index 11, 0-based) is the header row; we only use it for
    # positional reference — columns are fixed per the confirmed structure:
    #   0: Scheme Name
    #   1: Transaction Type
    #   2: Units
    #   3: NAV
    #   4: Amount
    #   5: Date
    COL_SCHEME   = 0
    COL_TX_TYPE  = 1
    COL_UNITS    = 2
    COL_NAV      = 3
    COL_AMOUNT   = 4
    COL_DATE     = 5

    # Data starts at row index 13 (row 14 in Excel, skipping the blank row 13)
    data_start = 13

    for row_idx, row in enumerate(all_rows[data_start:], start=data_start + 1):
        scheme_name = row[COL_SCHEME] if len(row) > COL_SCHEME else None

        # Skip blank rows
        if scheme_name is None or str(scheme_name).strip() == "":
            continue

        units = _to_float(row[COL_UNITS] if len(row) > COL_UNITS else 0, "Units")

        # Skip rows with zero or negative units
        if units <= 0:
            logger.warning("Row %d: skipping — units=%s", row_idx, units)
            continue

        nav    = _to_float(row[COL_NAV]    if len(row) > COL_NAV    else 0, "NAV")
        amount = _to_float(row[COL_AMOUNT] if len(row) > COL_AMOUNT else 0, "Amount")
        date   = _parse_date(row[COL_DATE] if len(row) > COL_DATE   else "")
        action = _parse_action(row[COL_TX_TYPE] if len(row) > COL_TX_TYPE else "")

        rows.append({
            "fund_name":     str(scheme_name).strip(),
            "isin":          "",          # Groww MF export does not include ISIN
            "date":          date,
            "action":        action,
            "units":         units,
            "nav":           nav,
            "amount":        amount,
            "broker":        "Groww",
            "import_source": filename,
        })

    wb.close()
    return rows
