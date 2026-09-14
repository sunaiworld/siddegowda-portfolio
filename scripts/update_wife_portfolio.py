#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Wife Portfolio Updater
Builds and updates the Wife_Portfolio tab in Google Sheets using trade data
from data/imports/Wife/ (Wife_Tradebook_2026.csv).

Applies the exact same table structure, column layout, number formatting,
and visual styling as the main Portfolio tab:
- Symbol, Investment Source, Shares, Avg Buy, CMP, Day Chg%, 1W/1M/3M/6M Return %,
  Invested, Value, P&L, Return %, Wt %, Stop Loss, Target, Buy More@, Signal.
- Currency and percentage number formatting.
- Canonical cell colorings: Stop Loss, Target, Buy More@, P&L, Return %, Signals.
- Filter, frozen headers, and subtotal row.

Usage:
  python scripts/update_wife_portfolio.py [--dry-run]
"""

import sys
import os
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from config import SHEET_ID, TECH_WORKERS
from sheet_writer import get_gspread_client
from portfolio_builder import load_wife_trades, compute_holdings, build_portfolio, write_portfolio
from data_fetcher import fetch_prices_batch, fetch_technicals
import fund_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("wife_portfolio")


def update_wife_portfolio(dry_run=False):
    log.info("Loading Wife trades from data/imports/Wife/...")
    trades = load_wife_trades()
    if not trades:
        log.warning("No trades found in data/imports/Wife/. Aborting.")
        return False

    holdings = compute_holdings(trades)
    log.info(f"Computed {len(holdings)} active holdings across {len(trades)} raw trade rows.")

    symbols = [h["symbol"] for h in holdings.values() if h.get("symbol")]
    log.info(f"Fetching current market prices for {len(symbols)} symbols...")
    prices = fetch_prices_batch(symbols)

    log.info(f"Fetching technical indicators for momentum columns ({TECH_WORKERS} workers)...")
    tech_map = {}
    with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
        futures = {ex.submit(fetch_technicals, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                tech_map[sym] = fut.result()
            except Exception as e:
                log.warning(f"  Failed technicals for {sym}: {e}")
                tech_map[sym] = {}

    log.info("Building Wife portfolio rows and metrics...")
    portfolio_dict = build_portfolio(prices, tech_map=tech_map, trades=trades)
    combined_rows = portfolio_dict.get("combined", [])

    tot_inv = sum(r["invested"] for r in combined_rows)
    tot_val = sum(r["value"] for r in combined_rows)
    tot_pnl = round(tot_val - tot_inv, 2)
    tot_ret = round((tot_pnl / tot_inv) * 100, 2) if tot_inv else 0.0

    log.info(f"Wife Portfolio Summary: {len(combined_rows)} holdings | Invested: ₹{tot_inv:,.2f} | Value: ₹{tot_val:,.2f} | P&L: ₹{tot_pnl:,.2f} ({tot_ret:.2f}%)")

    if dry_run:
        print("\n=== DRY RUN: WIFE PORTFOLIO ROWS ===")
        header = f"{'Symbol':<14} {'Source':<10} {'Shares':>8} {'Avg Buy':>10} {'CMP':>10} {'Invested':>12} {'Value':>12} {'P&L':>10} {'Return %':>9} {'Signal':<12}"
        print(header)
        print("-" * len(header))
        for r in combined_rows:
            print(f"{r['symbol']:<14} {r.get('investment_source',''):<10} {r['shares']:>8.1f} {r['avg_buy']:>10.2f} {r['cmp']:>10.2f} {r['invested']:>12.2f} {r['value']:>12.2f} {r['pnl']:>10.2f} {r['return_pct']:>8.2f}% {r.get('signal',''):<12}")
        print("-" * len(header))
        print(f"{'TOTAL':<14} {'':<10} {'':>8} {'':>10} {'':>10} {tot_inv:>12.2f} {tot_val:>12.2f} {tot_pnl:>10.2f} {tot_ret:>8.2f}%\n")
        log.info("Dry-run complete. No Google Sheets updates made.")
        return True

    log.info("Connecting to Google Sheets...")
    client = get_gspread_client()
    sh = client.open_by_key(SHEET_ID) if SHEET_ID else client.open("siddegowda-portfolio")

    log.info("Writing Wife_Portfolio worksheet with exact Portfolio tab style...")
    write_portfolio(sh, portfolio_dict, tab_name="Wife_Portfolio")
    log.info("✓ Wife_Portfolio tab successfully updated!")
    return True


def main():
    parser = argparse.ArgumentParser(description="Update Wife_Portfolio tab in Google Sheets")
    parser.add_argument("--dry-run", action="store_true", help="Calculate and print portfolio metrics without writing to Google Sheets")
    args = parser.parse_args()

    success = update_wife_portfolio(dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
