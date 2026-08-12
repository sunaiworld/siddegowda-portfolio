import os
import json
import time
import logging
import statistics
import requests
import math
from datetime import datetime, date, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials
import fund_cache
import history_tracker
import portfolio_analytics
import news_engine.news_cache as news_cache
from news_engine.sources import google_news_rss
from news_engine import classifier

log = logging.getLogger("portfolio")

from config import *
from sheet_formatter import *
from github_data_builder import build_result_row
from score_engine import *
from data_fetcher import fetch_prices_batch, fetch_technicals, fetch_rev_growth



# ══════════════════════════════════════════════
# WATCHLIST PROCESSING (Future Buy, and any future
# watchlist added to WATCHLISTS). Market-data only —
# no qty, no buy price, no purchase date, no XIRR/SL/
# target alerts. Duplicate symbols vs Portfolio/GITHUB
# DATA are independent here and never touch holdings
# or portfolio-value calculations.
# ══════════════════════════════════════════════
def process_watchlist_tab(sh, tab_name, symbols, nc_cache=None, shared_prices=None, shared_fund=None, shared_tech=None, shared_rev=None):
    if not symbols:
        log.warning(f"{tab_name}: no symbols configured, skipping")
        return []

    shared_prices = shared_prices or {}
    shared_fund = shared_fund or {}
    shared_tech = shared_tech or {}
    shared_rev = shared_rev or {}

    missing_syms = [s for s in symbols if s not in shared_prices]
    prices = shared_prices.copy()
    if missing_syms:
        log.info(f"{tab_name}: fetching prices for {len(missing_syms)} new symbols...")
        prices.update(fetch_prices_batch(missing_syms))

    wl_cache = fund_cache.load_cache(sh)
    
    missing_fund = [s for s in symbols if s not in shared_fund]
    fund_map = shared_fund.copy()
    if missing_fund:
        log.info(f"{tab_name}: fetching fundamentals for {len(missing_fund)} new symbols...")
        def _fetch_fund(sym):
            return sym, fund_cache.get_or_fetch_fundamentals(sym, wl_cache, max_age_days=FUNDAMENTALS_CACHE_DAYS)
        with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
            futures = {ex.submit(_fetch_fund, sym): sym for sym in missing_fund}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, data = fut.result()
                    fund_map[sym] = data
                except Exception as e:
                    fund_map[sym] = {}
        fund_cache.save_cache(sh, wl_cache)

    missing_tech = [s for s in symbols if s not in shared_tech]
    tech_map = shared_tech.copy()
    rev_map = shared_rev.copy()
    if missing_tech:
        log.info(f"{tab_name}: fetching tech/growth for {len(missing_tech)} new symbols...")
        def _fetch_tech(sym):
            return sym, fetch_technicals(sym), fetch_rev_growth(sym)
        with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
            futures = {ex.submit(_fetch_tech, sym): sym for sym in missing_tech}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, tech, rev = fut.result()
                    tech_map[sym] = tech
                    rev_map[sym] = rev
                except Exception as e:
                    tech_map[sym] = {}
                    rev_map[sym] = None

    rows = []
    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            log.warning(f"  SKIP {sym} ({tab_name}) — no price")
            continue

        f = fund_map.get(sym, {})
        tech = tech_map.get(sym, {})
        rev_gr = rev_map.get(sym)

        nd = (nc_cache or {}).get(sym.upper(), {})

        row, archetype, tot_sc, final_action = build_result_row(
            sym, cmp, f, tech, rev_gr, xirr_val="", news_data=nd if nd else None
        )
        rows.append(row)
        news_tag = f" | News:{nd.get('sentiment','')}" if nd.get('sentiment') else ""
        log.info(f"  {sym:12} | {archetype:25} | Total:{tot_sc:3} | {final_action}{news_tag}")

    write_github_data(sh, rows, tab_name=tab_name)
    return rows


def process_all_watchlists(sh, nc_cache=None, shared_prices=None, shared_fund=None, shared_tech=None, shared_rev=None):
    all_rows = {}
    for tab_name, symbols in WATCHLISTS.items():
        try:
            rows = process_watchlist_tab(
                sh, tab_name, symbols, nc_cache=nc_cache,
                shared_prices=shared_prices, shared_fund=shared_fund,
                shared_tech=shared_tech, shared_rev=shared_rev
            )
            all_rows[tab_name] = rows
        except Exception as e:
            log.error(f"Watchlist '{tab_name}' failed (existing tabs unaffected): {e}")
            all_rows[tab_name] = []
    return all_rows

