# scripts/import_trades.py
"""
Orchestrator for the trade‑import pipeline.

- Scans the broker‑specific import folders.
- Calls the appropriate importer for each file.
- De‑duplicates against the master CSV (broker + trade_id).
- Appends only new rows, keeps the master CSV sorted by ISO date.
- Prints a concise summary.
"""

import csv
import os
import sys
from pathlib import Path
from typing import List, Dict, Tuple
import pandas as pd
# Ensure project root is on sys.path for package imports
project_root = Path(__file__).parents[1]
sys.path.append(str(project_root))

# --------------------------------------------------------------------------- #
# Helpers – reading / writing the master CSV
# --------------------------------------------------------------------------- #
MASTER_CSV = Path(__file__).parents[1] / "data" / "trade_log.csv"
ZERODHA_DIR = Path(__file__).parents[1] / "data" / "imports" / "zerodha"
GROWW_DIR = Path(__file__).parents[1] / "data" / "imports" / "groww"

SCHEMA = [
    "trade_id",
    "date",
    "symbol",
    "exchange",
    "segment",
    "action",
    "quantity",
    "price",
    "gross_amount",
    "brokerage",
    "taxes_charges",
    "net_amount",
    "broker",
    "broker_order_id",
    "currency",
    "import_source",
    "notes",
]


def _load_master() -> Tuple[List[Dict[str, str]], set]:
    """Load existing master CSV (if any) and return rows + duplicate‑check set."""
    rows: List[Dict[str, str]] = []
    dup_set = set()  # (broker, trade_id)

    if MASTER_CSV.is_file():
        with open(MASTER_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                rows.append(r)
                dup_set.add((r["broker"], r["trade_id"]))
    return rows, dup_set


def _write_master(all_rows: List[Dict[str, str]]) -> None:
    """Write the (already sorted) list of rows back to trade_log.csv."""
    with open(MASTER_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SCHEMA)
        writer.writeheader()
        writer.writerows(all_rows)


# --------------------------------------------------------------------------- #
# Importer dispatchers
# --------------------------------------------------------------------------- #
def _import_zerodha_files() -> Tuple[int, List[Dict[str, str]]]:
    """Run the Zerodha importer on every CSV under imports/zerodha."""
    from data.imports.zerodha.import_zerodha import import_zerodha

    imported = 0
    new_rows: List[Dict[str, str]] = []

    for file_path in ZERODHA_DIR.glob("*.csv"):
        rows = import_zerodha(str(file_path))
        new_rows.extend(rows)
        imported += len(rows)

    return imported, new_rows


def _import_groww_files() -> Tuple[int, List[Dict[str, str]]]:
    """Run the Groww importer on every XLSX under imports/groww."""
    from data.imports.groww.import_groww import import_groww

    imported = 0
    new_rows: List[Dict[str, str]] = []

    for file_path in GROWW_DIR.glob("*.xlsx"):
        rows = import_groww(str(file_path))
        new_rows.extend(rows)
        imported += len(rows)

    return imported, new_rows


# --------------------------------------------------------------------------- #
# Main orchestration
# --------------------------------------------------------------------------- #
def main() -> None:
    # 1️⃣ Load master CSV & duplicate set
    master_rows, dup_set = _load_master()

    # Counters
    imported_counts = {"Zerodha": 0, "Groww": 0}
    duplicate_skipped = 0
    new_trade_rows: List[Dict[str, str]] = []

    # 2️⃣ Process Zerodha files
    zerodha_cnt, zerodha_rows = _import_zerodha_files()
    imported_counts["Zerodha"] = zerodha_cnt
    for row in zerodha_rows:
        key = (row["broker"], row["trade_id"])
        if key in dup_set:
            duplicate_skipped += 1
            continue
        dup_set.add(key)
        new_trade_rows.append(row)

    # 3️⃣ Process Groww files
    groww_cnt, groww_rows = _import_groww_files()
    imported_counts["Groww"] = groww_cnt
    for row in groww_rows:
        key = (row["broker"], row["trade_id"])
        if key in dup_set:
            duplicate_skipped += 1
            continue
        dup_set.add(key)
        new_trade_rows.append(row)

    # 4️⃣ Merge and sort by ISO date (lexicographic works for YYYY‑MM‑DD)
    all_rows = master_rows + new_trade_rows
    all_rows.sort(key=lambda r: r["date"])

    # 5️⃣ Write back
    _write_master(all_rows)

    # 6️⃣ Summary output
    print("\nImported:")
    print(f"Zerodha : {imported_counts['Zerodha']} trades")
    print(f"Groww   : {imported_counts['Groww']} trades")
    print(f"Skipped duplicates : {duplicate_skipped}")
    print(f"Total trade_log rows : {len(all_rows)}\n")


if __name__ == "__main__":
    # Ensure the script is being run from any location – paths are absolute
    try:
        main()
    except Exception as e:
        sys.stderr.write(f"❌ Import failed: {e}\n")
        sys.exit(1)
