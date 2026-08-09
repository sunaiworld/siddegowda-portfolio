#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Trade Import Pipeline
Idempotent append-only trade importer for Zerodha and Groww exports.
"""

import os
import csv
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# Master trade log file path
MASTER_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "trade_log.csv")

# Import folders
IMPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "imports")
ZERODHA_DIR = os.path.join(IMPORTS_DIR, "zerodha")
GROWW_DIR = os.path.join(IMPORTS_DIR, "groww")

# Schema column order
SCHEMA_HEADERS = [
    "trade_id", "date", "symbol", "exchange", "segment", "action",
    "quantity", "price", "gross_amount", "brokerage", "taxes_charges",
    "net_amount", "broker", "broker_order_id", "currency", "import_source", "notes"
]

# Field header aliases for mapping broker files to schema columns (case/symbol agnostic)
ALIASES = {
    "symbol": ["symbol", "tradingsymbol", "trading_symbol", "instrument", "instrumentname", "stock", "company", "companyname", "scrip"],
    "date": ["date", "tradedate", "trade_date", "time", "executiontime", "execution_time", "transactiondate", "transaction_date", "orderdate", "order_date"],
    "action": ["action", "type", "tradetype", "trade_type", "transaction", "transactiontype", "transaction_type", "buy/sell", "buysell"],
    "quantity": ["quantity", "qty", "vol", "volume", "shares", "nos", "qtyexecuted", "qty_executed"],
    "price": ["price", "tradeprice", "trade_price", "rate", "avgprice", "avg_price", "averageprice", "average_price", "executionprice", "execution_price"],
    "broker_order_id": ["orderid", "order_id", "orderno", "order_no", "order_number", "ordernumber", "transactionid", "transaction_id", "brokerorderid", "broker_order_id"],
    "exchange": ["exchange", "exch"],
    "segment": ["segment", "seg"],
}

def ensure_directories():
    """Create import directories if they do not exist."""
    os.makedirs(ZERODHA_DIR, exist_ok=True)
    os.makedirs(GROWW_DIR, exist_ok=True)

def read_master_trades():
    """Read existing trades from the master trade log."""
    if not os.path.exists(MASTER_LOG_PATH):
        return []
    try:
        with open(MASTER_LOG_PATH, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [{k.strip(): v.strip() for k, v in row.items() if k is not None} for row in reader if row]
    except Exception as e:
        log.warning(f"Could not read master trade log {MASTER_LOG_PATH}: {e}")
        return []

def get_column_by_aliases(row_dict, aliases):
    """Retrieve value from a row dict by matching any header alias."""
    for alias in aliases:
        a_clean = alias.lower().replace(" ", "").replace("_", "").replace("-", "")
        for key in row_dict.keys():
            k_clean = key.lower().replace(" ", "").replace("_", "").replace("-", "")
            if k_clean == a_clean:
                return row_dict[key]
    return None

def normalize_date(date_str):
    """Normalize broker date strings to DD-MM-YYYY."""
    if not date_str:
        raise ValueError("Date field is empty")
    date_str = str(date_str).strip()
    if " " in date_str:
        date_str = date_str.split(" ")[0]  # strip time portion
    
    # Try parsing different formats
    for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y"]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%d-%m-%Y")
        except ValueError:
            pass
    raise ValueError(f"Unknown date format: '{date_str}'")

def normalize_action(action_str):
    """Normalize buy/sell indicator to BUY or SELL."""
    if not action_str:
        raise ValueError("Action/Type field is empty")
    a = str(action_str).strip().upper()
    if a in ("BUY", "B", "BUYING", "PURCHASE"):
        return "BUY"
    if a in ("SELL", "S", "SELLING", "SALE"):
        return "SELL"
    raise ValueError(f"Unknown transaction type: '{action_str}'")

def find_header_row(rows):
    """Scan rows and return index of the first row containing core columns."""
    for idx, row in enumerate(rows):
        if not row:
            continue
        match_count = 0
        cleaned_cells = [str(cell).lower().strip().replace(" ", "").replace("_", "").replace("-", "") for cell in row if cell]
        
        core_fields = ["symbol", "date", "action", "quantity", "price"]
        for field in core_fields:
            for alias in ALIASES[field]:
                a_clean = alias.lower().replace(" ", "").replace("_", "").replace("-", "")
                if a_clean in cleaned_cells:
                    match_count += 1
                    break
        
        # If at least 3 core fields match, this is likely the header row
        if match_count >= 3:
            return idx
    return 0

def load_csv_rows(file_path):
    """Read a CSV file and convert it to row dictionaries based on detected headers."""
    try:
        with open(file_path, mode="r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            raw_rows = list(reader)
        if not raw_rows:
            return []
        
        header_idx = find_header_row(raw_rows)
        headers = [h.strip() for h in raw_rows[header_idx]]
        
        out = []
        for r in raw_rows[header_idx + 1:]:
            # Skip empty lines
            if not any(r):
                continue
            row_dict = {}
            for i, val in enumerate(r):
                if i < len(headers):
                    row_dict[headers[i]] = val.strip()
            out.append(row_dict)
        return out
    except Exception as e:
        log.warning(f"Could not load CSV file {file_path}: {e}")
        return []

def load_excel_rows(file_path):
    """Read an Excel file using pandas if available; otherwise warn and skip."""
    try:
        import pandas as pd
    except ImportError:
        log.warning(f"Skipping Excel file {file_path}: pandas/openpyxl is not installed. Convert to CSV or install dependencies.")
        return []
    
    try:
        df = pd.read_excel(file_path, header=None)
        raw_rows = []
        for _, r in df.iterrows():
            raw_rows.append([str(v).strip() if v is not None and not pd.isna(v) else "" for v in r])
        
        if not raw_rows:
            return []
        
        header_idx = find_header_row(raw_rows)
        headers = [str(h).strip() for h in raw_rows[header_idx]]
        
        out = []
        for r in raw_rows[header_idx + 1:]:
            if not any(r):
                continue
            row_dict = {}
            for i, val in enumerate(r):
                if i < len(headers):
                    row_dict[headers[i]] = val
            out.append(row_dict)
        return out
    except Exception as e:
        log.warning(f"Could not parse Excel file {file_path}: {e}")
        return []

def load_file_rows(file_path):
    """Router to load rows from either CSV or Excel file formats."""
    _, ext = os.path.splitext(file_path.lower())
    if ext == ".csv":
        return load_csv_rows(file_path)
    elif ext in (".xlsx", ".xls"):
        return load_excel_rows(file_path)
    else:
        log.warning(f"Unsupported file format skipped: {file_path}")
        return []

def get_trade_signature(trade):
    """Generate deduplication signature for a trade dictionary."""
    broker = trade.get("broker", "").strip().upper()
    order_id = trade.get("broker_order_id", "").strip()
    
    if order_id:
        return (broker, order_id)
    else:
        # Fallback signature
        return (
            trade.get("date", "").strip(),
            trade.get("symbol", "").strip().upper(),
            trade.get("action", "").strip().upper(),
            str(float(trade.get("quantity", 0))),
            str(float(trade.get("price", 0))),
            broker
        )

def get_next_trade_id_num(master_trades):
    """Scan existing master trades to determine the next numeric suffix for ID generation."""
    import re
    max_num = 0
    for t in master_trades:
        tid = t.get("trade_id", "")
        match = re.match(r"TRD(\d+)", tid)
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1

def sort_date_key(trade_dict):
    """Key function to sort trades chronologically by date."""
    d_str = trade_dict.get("date", "")
    try:
        return datetime.strptime(d_str, "%d-%m-%Y").date()
    except:
        return datetime.min.date()

def process_broker_directory(dir_path, broker_name, existing_sigs):
    """Parse files, normalize records, detect duplicates, and return new trade records."""
    if not os.path.exists(dir_path):
        return [], 0, 0
    
    new_trades = []
    files_processed = 0
    duplicates_skipped = 0
    
    for f_name in os.listdir(dir_path):
        f_path = os.path.join(dir_path, f_name)
        if os.path.isdir(f_path):
            continue
        
        log.info(f"Processing {broker_name} file: {f_name}")
        rows = load_file_rows(f_path)
        if not rows:
            continue
        
        files_processed += 1
        for row in rows:
            try:
                # Extract core fields using header aliases
                sym_raw = get_column_by_aliases(row, ALIASES["symbol"])
                date_raw = get_column_by_aliases(row, ALIASES["date"])
                act_raw = get_column_by_aliases(row, ALIASES["action"])
                qty_raw = get_column_by_aliases(row, ALIASES["quantity"])
                prc_raw = get_column_by_aliases(row, ALIASES["price"])
                order_raw = get_column_by_aliases(row, ALIASES["broker_order_id"]) or ""
                exch_raw = get_column_by_aliases(row, ALIASES["exchange"]) or "NSE"
                seg_raw = get_column_by_aliases(row, ALIASES["segment"]) or "EQ"
                
                # Check for mandatory fields
                if sym_raw is None or date_raw is None or act_raw is None or qty_raw is None or prc_raw is None:
                    missing = [k for k, v in {"symbol": sym_raw, "date": date_raw, "action": act_raw, "quantity": qty_raw, "price": prc_raw}.items() if v is None]
                    log.warning(f"  Skipping corrupted row (missing core fields {missing}): {row}")
                    continue
                
                # Normalization
                symbol = str(sym_raw).strip().upper()
                date_val = normalize_date(date_raw)
                action = normalize_action(act_raw)
                quantity = abs(float(str(qty_raw).strip().replace(",", "")))
                price = float(str(prc_raw).strip().replace(",", ""))
                
                if quantity <= 0 or price <= 0:
                    log.warning(f"  Skipping row with invalid quantity/price: {row}")
                    continue
                
                # Gross calculation
                gross_amount = round(quantity * price, 2)
                
                # Build unified record
                trade_record = {
                    "trade_id": "", # Generated later
                    "date": date_val,
                    "symbol": symbol,
                    "exchange": str(exch_raw).strip().upper(),
                    "segment": str(seg_raw).strip().upper(),
                    "action": action,
                    "quantity": str(quantity),
                    "price": str(price),
                    "gross_amount": str(gross_amount),
                    "brokerage": "0.0",
                    "taxes_charges": "0.0",
                    "net_amount": str(gross_amount),
                    "broker": broker_name,
                    "broker_order_id": str(order_raw).strip(),
                    "currency": "INR",
                    "import_source": "import",
                    "notes": ""
                }
                
                # Check duplicate
                sig = get_trade_signature(trade_record)
                if sig in existing_sigs:
                    duplicates_skipped += 1
                else:
                    new_trades.append(trade_record)
                    existing_sigs.add(sig)
                    
            except Exception as e:
                log.warning(f"  Skipping corrupted row due to parsing error ({e}): {row}")
                continue
                
    return new_trades, files_processed, duplicates_skipped

def main():
    log.info("=" * 60)
    log.info("SIDDEGOWDA PORTFOLIO — Starting Trade Import Process")
    log.info("=" * 60)
    
    ensure_directories()
    
    # 1. Load current master trades
    master_trades = read_master_trades()
    log.info(f"Loaded {len(master_trades)} existing master trades.")
    
    # 2. Build master signatures set
    existing_sigs = set()
    for t in master_trades:
        existing_sigs.add(get_trade_signature(t))
        
    # 3. Process Zerodha directory
    zerodha_new, z_files, z_dups = process_broker_directory(ZERODHA_DIR, "Zerodha", existing_sigs)
    
    # 4. Process Groww directory
    groww_new, g_files, g_dups = process_broker_directory(GROWW_DIR, "Groww", existing_sigs)
    
    new_trades_all = zerodha_new + groww_new
    
    if new_trades_all:
        log.info(f"Assigning trade IDs for {len(new_trades_all)} new trades...")
        next_id_num = get_next_trade_id_num(master_trades)
        for t in new_trades_all:
            t["trade_id"] = f"TRD{next_id_num:05d}"
            next_id_num += 1
            
        master_trades.extend(new_trades_all)
        
        # 5. Sort master list chronologically
        master_trades.sort(key=sort_date_key)
        
        # 6. Save back to CSV
        os.makedirs(os.path.dirname(MASTER_LOG_PATH), exist_ok=True)
        try:
            with open(MASTER_LOG_PATH, mode="w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=SCHEMA_HEADERS)
                writer.writeheader()
                writer.writerows(master_trades)
            log.info("Successfully updated master trade log.")
        except Exception as e:
            log.error(f"Failed to write to master trade log {MASTER_LOG_PATH}: {e}")
            return
    else:
        log.info("No new trades found to import.")
        
    # Print the requested summary reports
    print("\n" + "=" * 40)
    print("IMPORT SUMMARY REPORT")
    print("=" * 40)
    
    # Zerodha report
    print(f"Broker: Zerodha")
    print(f"Files processed: {z_files}")
    print(f"Trades imported: {len(zerodha_new)}")
    print(f"Duplicates skipped: {z_dups}")
    print(f"Total master trades: {len(master_trades)}")
    print("-" * 40)
    
    # Groww report
    print(f"Broker: Groww")
    print(f"Files processed: {g_files}")
    print(f"Trades imported: {len(groww_new)}")
    print(f"Duplicates skipped: {g_dups}")
    print(f"Total master trades: {len(master_trades)}")
    print("=" * 40)

if __name__ == "__main__":
    main()
