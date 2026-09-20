"""
src/smallcase_loader.py
Dynamic smallcase data loader and exclusion engine.

Responsible for:
1. Auto-discovering all smallcase CSV files in data/imports/Zerodha_Smallcase/
   and data/imports/Groww_Smallcase/ (or any *Smallcase* import directories).
2. Dynamically extracting smallcase names from file names (or content) and
   normalizing stock symbols.
3. Providing unique smallcase symbol exclusion sets to ensure the Portfolio tab
   contains ONLY independently purchased/held stocks.
4. Generating comprehensive debug/audit reports tracking which smallcases caused
   each exclusion, including multi-smallcase holdings.
"""

import os
import re
import csv
import glob
import logging
from typing import Dict, List, Set, Tuple, Any

log = logging.getLogger("smallcase_loader")


def normalize_symbol(sym: str) -> str:
    """
    Consistently normalize a stock ticker symbol.
    Handles:
      - Whitespace stripping and uppercase conversion.
      - Exchange suffixes like .NS, .BO.
      - Exchange prefixes like NSE:, BSE:.
    """
    if not sym:
        return ""
    s = str(sym).strip().upper()
    # Strip common exchange suffixes
    s = re.sub(r"\.(NS|BO)$", "", s)
    # Strip common exchange prefixes
    if ":" in s:
        s = s.split(":")[-1]
    return s.strip()


def parse_smallcase_name(file_path: str) -> str:
    """
    Derives a human-readable smallcase name from a smallcase export file name.
    Example:
      'Electric_Mobility_Theme_20_9_2026.csv' -> 'Electric Mobility Theme'
      'Growth_Value_Multicap_Model_20_9_2026.csv' -> 'Growth & Value Multicap Model'
    """
    base = os.path.splitext(os.path.basename(file_path))[0]
    # Strip date patterns like _20_9_2026, _2026-09-20, _20260920, _01_01_2024
    clean = re.sub(r"_\d{1,2}_\d{1,2}_\d{2,4}$", "", base)
    clean = re.sub(r"_\d{4}[-_]\d{2}[-_]\d{2}$", "", clean)
    clean = re.sub(r"_\d{8}$", "", clean)
    
    # Replace underscores with spaces
    name = clean.replace("_", " ").strip()
    
    # Beautify known title variants if desired
    if name.lower() == "growth value multicap model":
        return "Growth & Value Multicap Model"
    return name


def _resolve_imports_dir(imports_dir: str = "data/imports") -> str:
    """Resolve imports directory relative to cwd, file location, or repo root."""
    candidates = [
        imports_dir,
        os.path.join(os.getcwd(), imports_dir),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", imports_dir),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), imports_dir),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.abspath(c)
    return os.path.abspath(imports_dir)


def load_all_smallcases(imports_dir: str = "data/imports") -> Tuple[Dict[str, List[Dict[str, str]]], Set[str]]:
    """
    Scans all smallcase directories under data/imports/ and parses all CSV files.
    
    Returns:
      (smallcase_map, smallcase_symbols)
      - smallcase_map: {SYMBOL: [{'smallcase': name, 'source': folder, 'file': filename}, ...]}
      - smallcase_symbols: set of normalized symbols across all smallcases
    """
    root_imports = _resolve_imports_dir(imports_dir)
    
    # Find all smallcase directories (case-insensitive search for 'smallcase')
    target_dirs = []
    if os.path.isdir(root_imports):
        for entry in os.listdir(root_imports):
            full_path = os.path.join(root_imports, entry)
            if os.path.isdir(full_path) and "smallcase" in entry.lower():
                target_dirs.append(full_path)
                
    if not target_dirs:
        # Fallback to standard directory names if not matched
        for standard in ["Zerodha_Smallcase", "Groww_Smallcase"]:
            cand = os.path.join(root_imports, standard)
            if os.path.isdir(cand) and cand not in target_dirs:
                target_dirs.append(cand)

    smallcase_map: Dict[str, List[Dict[str, str]]] = {}
    total_files = 0

    for folder in sorted(target_dirs):
        folder_name = os.path.basename(folder)
        csv_files = sorted(glob.glob(os.path.join(folder, "*.csv")))
        
        for f_path in csv_files:
            total_files += 1
            file_name = os.path.basename(f_path)
            sc_name = parse_smallcase_name(f_path)
            
            try:
                with open(f_path, mode="r", encoding="utf-8-sig") as fp:
                    reader = csv.reader(fp)
                    header_found = False
                    ticker_col = -1
                    name_col = -1
                    
                    for row in reader:
                        if not row or not any(row):
                            continue
                            
                        if not header_found:
                            cleaned_cells = [str(c).strip().lower() for c in row]
                            for i, c in enumerate(cleaned_cells):
                                if c in ("ticker", "symbol", "tradingsymbol", "trading symbol", "stock symbol"):
                                    ticker_col = i
                                if c in ("name", "company", "stock name", "company name"):
                                    name_col = i
                                    
                            if ticker_col >= 0:
                                header_found = True
                            continue
                            
                        if header_found and len(row) > ticker_col:
                            raw_sym = row[ticker_col]
                            sym = normalize_symbol(raw_sym)
                            if not sym:
                                continue
                                
                            entry_meta = {
                                "smallcase": sc_name,
                                "source": folder_name,
                                "file": file_name,
                                "company_name": row[name_col].strip() if name_col >= 0 and len(row) > name_col else ""
                            }
                            
                            if sym not in smallcase_map:
                                smallcase_map[sym] = []
                            if not any(e["file"] == file_name and e["smallcase"] == sc_name for e in smallcase_map[sym]):
                                smallcase_map[sym].append(entry_meta)
                                
            except Exception as e:
                log.warning(f"Failed to read smallcase file {file_name}: {e}")

    unique_symbols = set(smallcase_map.keys())
    log.info(
        f"Loaded {len(unique_symbols)} unique smallcase symbols across {total_files} "
        f"files in {len(target_dirs)} smallcase folders"
    )
    return smallcase_map, unique_symbols


def get_smallcase_exclusion_set(imports_dir: str = "data/imports") -> Set[str]:
    """Convenience helper returning only the set of normalized smallcase symbols."""
    _, symbols = load_all_smallcases(imports_dir)
    return symbols


def generate_smallcase_audit(
    broker_symbols: Set[str],
    smallcase_map: Dict[str, List[Dict[str, str]]],
    output_dir: str = "data"
) -> Dict[str, Any]:
    """
    Generates verification and audit reports for smallcase exclusions.
    
    Creates:
      1. data/smallcase_exclusion_audit.txt (Detailed summary and table)
      2. data/smallcase_excluded_stocks.csv (Machine-readable CSV)
      
    Returns a dictionary of audit summary statistics.
    """
    normalized_broker = {normalize_symbol(s) for s in broker_symbols if s}
    sc_symbols = set(smallcase_map.keys())
    
    excluded_symbols = sorted(normalized_broker & sc_symbols)
    remaining_symbols = sorted(normalized_broker - sc_symbols)
    
    stats = {
        "total_broker_symbols": len(normalized_broker),
        "total_smallcase_symbols": len(sc_symbols),
        "excluded_count": len(excluded_symbols),
        "remaining_portfolio_count": len(remaining_symbols),
        "excluded_symbols": excluded_symbols,
        "remaining_symbols": remaining_symbols,
    }
    
    # Resolve project root
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dir = os.path.join(repo_root, output_dir)
    os.makedirs(target_dir, exist_ok=True)
    
    txt_path = os.path.join(target_dir, "smallcase_exclusion_audit.txt")
    csv_path = os.path.join(target_dir, "smallcase_excluded_stocks.csv")
    
    # 1. Write text audit report
    lines = []
    lines.append("=" * 80)
    lines.append("SIDDEGOWDA PORTFOLIO — SMALLCASE EXCLUSION AUDIT REPORT")
    lines.append("=" * 80)
    lines.append(f"Total Broker Holdings Symbols : {stats['total_broker_symbols']}")
    lines.append(f"Total Unique Smallcase Symbols: {stats['total_smallcase_symbols']}")
    lines.append(f"Number of Excluded Symbols    : {stats['excluded_count']}")
    lines.append(f"Remaining Portfolio Symbols   : {stats['remaining_portfolio_count']}")
    lines.append("-" * 80)
    lines.append(f"{'Symbol':<15} | {'Smallcase(s)':<45} | {'Source'}")
    lines.append("-" * 80)
    
    for sym in excluded_symbols:
        entries = smallcase_map.get(sym, [])
        sc_names = ", ".join(dict.fromkeys(e["smallcase"] for e in entries))
        sources = ", ".join(dict.fromkeys(e["source"] for e in entries))
        lines.append(f"{sym:<15} | {sc_names:<45} | {sources}")
        
    lines.append("=" * 80)
    lines.append(f"\nRemaining Independent Portfolio Symbols ({len(remaining_symbols)}):")
    lines.append(", ".join(remaining_symbols))
    lines.append("")
    
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    # 2. Write machine-readable CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Symbol", "Smallcases", "Sources", "File_Origins"])
        for sym in excluded_symbols:
            entries = smallcase_map.get(sym, [])
            sc_names = "; ".join(dict.fromkeys(e["smallcase"] for e in entries))
            sources = "; ".join(dict.fromkeys(e["source"] for e in entries))
            files = "; ".join(dict.fromkeys(e.get("file", "") for e in entries))
            writer.writerow([sym, sc_names, sources, files])
            
    log.info(f"Generated smallcase exclusion audit at {txt_path} and {csv_path}")
    return stats
