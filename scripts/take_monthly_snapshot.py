#!/usr/bin/env python3
"""
Manual / Triggered Monthly Snapshot Generator
Captures point-in-time snapshots of GITHUB DATA and Future Buy.
"""

import sys
import os
import argparse
import logging
from datetime import datetime

# Add src to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import SHEET_ID
from sheet_writer import get_gspread_client
import monthly_snapshot
from main import run_portfolio_update

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Generate monthly snapshot for GITHUB DATA and Future Buy")
    parser.add_argument("--force", action="store_true", help="Force snapshot creation even if not last trading day or duplicate")
    parser.add_argument("--date", type=str, default=None, help="Custom snapshot date in YYYY-MM-DD format")
    args = parser.parse_args()

    log.info("Connecting to Google Sheets...")
    gc = get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)

    log.info("Running full pipeline to obtain point-in-time data...")
    update_result = run_portfolio_update(sh)

    if not update_result:
        log.error("Pipeline run returned no data. Aborting snapshot.")
        sys.exit(1)

    log.info("Executing monthly snapshot check/recording...")
    res = monthly_snapshot.check_and_record_monthly_snapshots(
        sh,
        github_results=update_result.get("results", []),
        future_buy_rows=update_result.get("watchlist_opportunities", []),
        sector_weights=None,
        portfolio_value=update_result.get("portfolio_live_value"),
        force=args.force,
        run_date=args.date
    )

    log.info(f"Snapshot Result: {res}")


if __name__ == "__main__":
    main()
