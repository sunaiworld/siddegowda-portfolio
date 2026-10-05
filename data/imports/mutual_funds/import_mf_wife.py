# imports/mutual_funds/import_mf_wife.py
"""
Wife Mutual Fund importer.
Supports:
1. Zerodha Coin Holdings CSV exports (e.g. Wife_Mutual_Funds_2024.csv, Wife_Mutual_Funds_2025.csv,
   Wife_Mutual_Funds_2026.csv, Wife_Mutual_Funds_XB1X0_20261003.csv).
   Synthesizes chronological FIFO trade lots across yearly snapshots so that holding periods,
   LTCG/STCG, and tax harvesting rules work accurately.
2. Zerodha MF tradebooks (tradebook-*-MF_*.csv).
3. Groww MF Order History XLSX exports.
"""

import csv
import glob
import logging
import os
import re
from datetime import datetime
from typing import Dict, List

logger = logging.getLogger(__name__)


def _to_float(val, default=0.0) -> float:
    try:
        return float(str(val).replace(",", "").strip())
    except Exception:
        return default


def _extract_date_or_year(filename: str) -> str:
    """Extract YYYY-MM-DD or YYYY from filename like Wife_Mutual_Funds_XB1X0_20261003.csv or Wife_Mutual_Funds_2024.csv."""
    m_full = re.search(r"202\d[01]\d[0-3]\d", filename)
    if m_full:
        d_str = m_full.group(0)
        try:
            return datetime.strptime(d_str, "%Y%m%d").strftime("%Y-%m-%d")
        except Exception:
            pass

    m_yr = re.search(r"202\d", filename)
    if m_yr:
        yr = m_yr.group(0)
        return f"{yr}-03-31"

    return "2024-01-01"


def import_mf_wife(target_path: str) -> List[Dict]:
    """
    Imports Wife mutual fund data from a directory (e.g. data/imports/Wife) or single file.
    Returns canonical trade dicts:
        fund_name, isin, date, action, units, nav, amount, broker, import_source
    """
    if os.path.isfile(target_path):
        wife_dir = os.path.dirname(os.path.abspath(target_path))
    else:
        wife_dir = os.path.abspath(target_path)

    if not os.path.isdir(wife_dir):
        logger.warning(f"Wife directory {wife_dir} does not exist")
        return []

    # Check for Zerodha Coin Holdings CSV files
    csv_files = glob.glob(os.path.join(wife_dir, "Wife_Mutual_Funds*.csv"))
    if not csv_files:
        csv_files = glob.glob(os.path.join(wife_dir, "*Mutual_Funds*.csv"))

    if csv_files:
        return _import_wife_holdings_csvs(csv_files)

    # Check for xlsx files (Groww export)
    xlsx_files = glob.glob(os.path.join(wife_dir, "*.xlsx"))
    if xlsx_files:
        try:
            from data.imports.mutual_funds.import_mf_groww import import_mf_groww
        except ImportError:
            here = os.path.dirname(os.path.abspath(__file__))
            g_path = os.path.join(here, "import_mf_groww.py")
            import importlib.util
            spec = importlib.util.spec_from_file_location("_import_mf_groww", g_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            import_mf_groww = mod.import_mf_groww

        trades = []
        for path in sorted(xlsx_files):
            try:
                rows = import_mf_groww(path)
                for r in rows:
                    r["broker"] = "Wife"
                trades.extend(rows)
            except Exception as e:
                logger.warning(f"Failed to import Groww XLSX for Wife {path}: {e}")
        return trades

    # Check for Zerodha tradebooks
    tb_files = glob.glob(os.path.join(wife_dir, "tradebook-*-MF_*.csv"))
    if tb_files:
        try:
            from data.imports.mutual_funds.import_mf_zerodha import import_mf_zerodha
        except ImportError:
            here = os.path.dirname(os.path.abspath(__file__))
            z_path = os.path.join(here, "import_mf_zerodha.py")
            import importlib.util
            spec = importlib.util.spec_from_file_location("_import_mf_zerodha", z_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            import_mf_zerodha = mod.import_mf_zerodha

        trades = []
        for path in sorted(tb_files):
            try:
                rows = import_mf_zerodha(path)
                for r in rows:
                    r["broker"] = "Wife"
                trades.extend(rows)
            except Exception as e:
                logger.warning(f"Failed to import Zerodha tradebook for Wife {path}: {e}")
        return trades

    logger.warning(f"No recognised Wife MF files found in {wife_dir}")
    return []


def _import_wife_holdings_csvs(csv_files: List[str]) -> List[Dict]:
    """
    Parses Zerodha Coin Holdings CSV files across snapshot periods.
    Deduplicates snapshots by year/date, then creates lot additions chronologically
    so that current active units and invested amount match the latest holdings statement exactly.
    """
    snapshots_by_key = {}
    for f in csv_files:
        base = os.path.basename(f)
        m = re.search(r"202\d{1,5}", base)
        if not m:
            continue
        key = m.group(0)[:4]  # e.g. "2024", "2025", "2026"
        if key not in snapshots_by_key or len(base) > len(os.path.basename(snapshots_by_key[key])):
            snapshots_by_key[key] = f

    if not snapshots_by_key:
        return []

    yearly_data = {}
    snapshot_dates = {}

    for yr in sorted(snapshots_by_key.keys()):
        filepath = snapshots_by_key[yr]
        filename = os.path.basename(filepath)
        snapshot_dates[yr] = _extract_date_or_year(filename)

        with open(filepath, newline="", encoding="utf-8-sig") as fp:
            reader = csv.reader(fp)
            try:
                header = [c.strip() for c in next(reader)]
            except StopIteration:
                continue

            fund_map = {}
            for row in reader:
                if not row or row[0].strip() == "Mutual Funds":
                    continue
                d = {k: v.strip() for k, v in zip(header, row)}
                isin = d.get("ISIN", "").strip()
                if not isin or not isin.startswith("INF"):
                    continue

                units = _to_float(d.get("Quantity", 0))
                inv = _to_float(d.get("Invested Value", 0))
                avg_cost = _to_float(d.get("Avg Unit Cost", d.get("Avg Unit Cost  ", 0)))
                name = d.get("Script Name", "").strip()

                fund_map[isin] = {
                    "fund_name": name,
                    "isin": isin,
                    "units": units,
                    "invested": inv,
                    "avg_cost": avg_cost,
                    "source": filename,
                }
            yearly_data[yr] = fund_map

    years = sorted(yearly_data.keys())
    latest_yr = years[-1]
    latest_map = yearly_data[latest_yr]

    trades: List[Dict] = []

    # If only 1 year available, synthesize directly from latest
    if len(years) == 1:
        trade_date = snapshot_dates[latest_yr]
        source_file = os.path.basename(snapshots_by_key[latest_yr])
        for isin, cur in sorted(latest_map.items()):
            trades.append({
                "fund_name": cur["fund_name"],
                "isin": isin,
                "date": trade_date,
                "action": "buy",
                "units": cur["units"],
                "nav": cur["avg_cost"],
                "amount": cur["invested"],
                "broker": "Wife",
                "import_source": source_file,
            })
        return trades

    # Multi-year available (e.g. 2024, 2025, 2026)
    # Reconstruct lots backwards to preserve exact current active units & cost basis
    h24 = yearly_data.get("2024", {})
    h25 = yearly_data.get("2025", {})
    h26 = latest_map

    date_24 = snapshot_dates.get("2024", "2024-03-31")
    date_25 = snapshot_dates.get("2025", "2025-03-31")
    date_26 = snapshot_dates.get(latest_yr, "2026-10-03")

    src_24 = os.path.basename(snapshots_by_key.get("2024", "Wife_Mutual_Funds_2024.csv"))
    src_25 = os.path.basename(snapshots_by_key.get("2025", "Wife_Mutual_Funds_2025.csv"))
    src_26 = os.path.basename(snapshots_by_key[latest_yr])

    for isin, cur in sorted(h26.items()):
        q26 = cur["units"]
        inv26 = cur["invested"]
        fn = cur["fund_name"]

        d25 = h25.get(isin, {"units": 0.0, "invested": 0.0})
        d24 = h24.get(isin, {"units": 0.0, "invested": 0.0})

        q25, inv25 = d25["units"], d25["invested"]
        q24, inv24 = d24["units"], d24["invested"]

        u26_add = max(0.0, q26 - q25)
        u25_add = max(0.0, min(q26, q25) - q24)
        u24_base = max(0.0, q26 - u26_add - u25_add)

        inv26_add = max(0.0, inv26 - inv25) if u26_add > 0 else 0.0
        inv25_add = max(0.0, inv25 - inv24) if u25_add > 0 else 0.0
        inv24_base = round(inv26 - inv26_add - inv25_add, 2)

        # Tranche 1: 2024 lot (held > 365 days -> LTCG)
        if u24_base > 0:
            nav_24 = round(inv24_base / u24_base, 4) if u24_base > 0 else cur["avg_cost"]
            trades.append({
                "fund_name": fn,
                "isin": isin,
                "date": date_24,
                "action": "buy",
                "units": round(u24_base, 4),
                "nav": nav_24,
                "amount": inv24_base,
                "broker": "Wife",
                "import_source": src_24,
            })

        # Tranche 2: 2025 lot (held > 365 days -> LTCG)
        if u25_add > 0:
            nav_25 = round(inv25_add / u25_add, 4) if u25_add > 0 else cur["avg_cost"]
            trades.append({
                "fund_name": fn,
                "isin": isin,
                "date": date_25,
                "action": "buy",
                "units": round(u25_add, 4),
                "nav": nav_25,
                "amount": inv25_add,
                "broker": "Wife",
                "import_source": src_25,
            })

        # Tranche 3: 2026 addition lot (<= 365 days -> STCG)
        if u26_add > 0:
            nav_26 = round(inv26_add / u26_add, 4) if u26_add > 0 else cur["avg_cost"]
            trades.append({
                "fund_name": fn,
                "isin": isin,
                "date": date_26,
                "action": "buy",
                "units": round(u26_add, 4),
                "nav": nav_26,
                "amount": inv26_add,
                "broker": "Wife",
                "import_source": src_26,
            })

    return trades
