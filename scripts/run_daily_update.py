#!/usr/bin/env python3
"""
InvestmentBrain Daily Update Orchestrator
Coordinates trade imports and portfolio updates into a single daily workflow.
"""

import sys
import os
import subprocess
import time
import re

def safe_print(text):
    """Print text, falling back to ASCII checkmarks/symbols if console encoding fails on Windows."""
    try:
        print(text)
    except UnicodeEncodeError:
        # Fallback for older Windows cmd shells
        print(text.replace("✓", "[OK]").replace("✗", "[FAIL]").replace("↓", "v"))

def main():
    start_time = time.time()
    
    # Paths to sub-scripts
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    importer_path = os.path.join(base_dir, "scripts", "import_trades.py")
    engine_path = os.path.join(base_dir, "src", "main.py")
    
    safe_print("==================================================")
    safe_print("InvestmentBrain Daily Update")
    safe_print("==================================================")
    safe_print("")
    
    # ----------------------------------------------------
    # Step 1: Run Trade Importer
    # ----------------------------------------------------
    safe_print("Importing trades...")
    
    import_proc = subprocess.run(
        [sys.executable, importer_path],
        capture_output=True,
        text=True
    )
    
    if import_proc.returncode != 0:
        safe_print("")
        safe_print("✗ Trade import FAILED!")
        safe_print(f"Error Output:\n{import_proc.stderr or import_proc.stdout}")
        sys.exit(import_proc.returncode)
        
    # Parse import stats from output
    stdout = import_proc.stdout
    
    zerodha_match = re.search(r"Broker:\s+Zerodha\s+Files processed:\s+\d+\s+Trades imported:\s+(\d+)", stdout)
    groww_match = re.search(r"Broker:\s+Groww\s+Files processed:\s+\d+\s+Trades imported:\s+(\d+)", stdout)
    
    z_imported = int(zerodha_match.group(1)) if zerodha_match else 0
    g_imported = int(groww_match.group(1)) if groww_match else 0
    
    if z_imported == 0 and g_imported == 0:
        safe_print("  No new trades found.")
    else:
        if z_imported > 0:
            safe_print(f"✓ Zerodha : {z_imported} imported")
        if g_imported > 0:
            safe_print(f"✓ Groww   : {g_imported} imported")
            
    safe_print("")
    
    # ----------------------------------------------------
    # Step 2: Run Portfolio Engine Daily Update
    # ----------------------------------------------------
    safe_print("Updating Portfolio...")
    
    engine_proc = subprocess.run(
        [sys.executable, engine_path],
        capture_output=True,
        text=True
    )
    
    if engine_proc.returncode != 0:
        safe_print("")
        safe_print("✗ Portfolio engine update FAILED!")
        safe_print("Traceback / Error Details:")
        safe_print(engine_proc.stderr or engine_proc.stdout)
        sys.exit(engine_proc.returncode)
        
    # If successful, print success reports
    safe_print("")
    safe_print("✓ Portfolio updated")
    safe_print("✓ GITHUB DATA updated")
    safe_print("✓ Future Buy updated")
    safe_print("✓ History updated")
    safe_print("✓ Portfolio History updated")
    safe_print("")
    
    runtime = int(round(time.time() - start_time))
    
    safe_print("Completed successfully.")
    safe_print(f"Total Runtime : {runtime} seconds")
    safe_print("==================================================")
    
    # Print the profiler summary which is captured in stdout
    if "PERFORMANCE SUMMARY" in engine_proc.stdout:
        safe_print(engine_proc.stdout[engine_proc.stdout.find("========== PERFORMANCE SUMMARY =========="):])

if __name__ == "__main__":
    main()
