#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Mutual Funds Tab Updater
Loads all mutual fund trades and holdings:
- Zerodha (Self) from data/imports/zerodha/tradebook-*-MF_*.csv
- Groww (Dad) from data/imports/groww/Mutual_Funds_*.xlsx
- Wife from data/imports/Wife/ (holdings CSVs, tradebooks, or XLSX)
Computes holdings, tax harvesting, fund overlap, and stock overlap,
and writes to the 'Mutual Funds' worksheet in Google Sheets.

Usage:
  python scripts/update_mutual_funds.py
"""

import os
import sys
import json
import logging
from google.oauth2.service_account import Credentials
import gspread

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import mutual_fund_builder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("update_mutual_funds")


def get_spreadsheet():
    sheet_id = os.environ.get("SHEET_ID", "").strip() or "1Flm7u1ik-C_2hZ5_GTTvlymQmJiyneWPdplB7zFrC_I"
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()

    if not creds_json:
        creds_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "InvestmentBrain",
                "90_System_&_Automation",
                "01_Scripts",
                "credentials.json",
            )
        )
        if os.path.isfile(creds_path):
            log.info(f"Using local credentials from {creds_path}")
            with open(creds_path, encoding="utf-8") as f:
                creds_json = f.read()
            os.environ["GOOGLE_CREDENTIALS_JSON"] = creds_json
            os.environ["SHEET_ID"] = sheet_id
        else:
            raise ValueError("GOOGLE_CREDENTIALS_JSON not found in environment or local path.")

    creds_obj = json.loads(creds_json)
    creds = Credentials.from_service_account_info(
        creds_obj,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    log.info(f"Connected to Google Sheet: {sh.title} ({sheet_id})")
    return sh


def main():
    sh = get_spreadsheet()
    log.info("Running Mutual Fund update pipeline...")
    mutual_fund_builder.run_mutual_fund_update(sh)
    log.info("✓ Mutual Funds tab successfully updated!")


if __name__ == "__main__":
    main()
