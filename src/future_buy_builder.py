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
from score_engine import *



# ══════════════════════════════════════════════
# WATCHLIST PROCESSING (Future Buy, and any future
# watchlist added to WATCHLISTS). Market-data only —
# no qty, no buy price, no purchase date, no XIRR/SL/
# target alerts. Duplicate symbols vs Portfolio/GITHUB
# DATA are independent here and never touch holdings
# or portfolio-value calculations.
# ══════════════════════════════════════════════
def process_watchlist_tab(sh, tab_name, symbols, nc_cache=None):
    """
    nc_cache: the in-memory news cache dict already built by
    run_portfolio_update() (keyed by symbol.upper()). When provided,
    watchlist symbols read news from it at zero cost — no extra fetch.
    When None (e.g. standalone call), news columns are left blank.
    """
    if not symbols:
        log.warning(f"{tab_name}: no symbols configured, skipping")
        return []

    log.info(f"{tab_name}: fetching prices for {len(symbols)} symbols...")
    prices = fetch_prices_batch(symbols)
    wl_cache = fund_cache.load_cache(sh)

    rows = []
    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            log.warning(f"  SKIP {sym} ({tab_name}) — no price")
            continue

        f      = fund_cache.get_or_fetch_fundamentals(sym, wl_cache, max_age_days=FUNDAMENTALS_CACHE_DAYS)
        rev_gr = fetch_rev_growth(sym)
        tech   = fetch_technicals(sym)
        time.sleep(SLEEP_INFO)

        # Read news from the shared cache (built in run_portfolio_update).
        # Watchlist symbols that weren't in the portfolio won't have a
        # cache entry yet — nd will be {} and news columns stay blank.
        # No new fetch is triggered here: the 6-hour refresh cycle is
        # handled exclusively by run_portfolio_update() for portfolio
        # symbols; watchlist-only symbols pick up news on the next run
        # after they enter the portfolio, or can be added separately.
        nd = (nc_cache or {}).get(sym.upper(), {})

        row, archetype, tot_sc, final_action = build_result_row(
            sym, cmp, f, tech, rev_gr, xirr_val="", news_data=nd if nd else None
        )
        rows.append(row)
        news_tag = f" | News:{nd.get('sentiment','')}" if nd.get('sentiment') else ""
        log.info(f"  {sym:12} | {archetype:25} | Total:{tot_sc:3} | {final_action}{news_tag}")

    fund_cache.save_cache(sh, wl_cache)
    write_github_data(sh, rows, tab_name=tab_name)
    return rows


def process_all_watchlists(sh, nc_cache=None):
    """
    Run every watchlist tab and return a dict:
      {tab_name: [row, ...]}  (same row layout as GITHUB DATA)
    Tabs with errors return [] so the caller can still use the rest.

    nc_cache: pass the news cache dict from run_portfolio_update() so
    watchlist symbols receive populated news columns and the news timing
    modifier without any additional API calls.
    """
    all_rows = {}
    for tab_name, symbols in WATCHLISTS.items():
        try:
            rows = process_watchlist_tab(sh, tab_name, symbols, nc_cache=nc_cache)
            all_rows[tab_name] = rows
        except Exception as e:
            log.error(f"Watchlist '{tab_name}' failed (existing tabs unaffected): {e}")
            all_rows[tab_name] = []
    return all_rows

