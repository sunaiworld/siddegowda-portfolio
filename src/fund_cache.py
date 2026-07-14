"""
Fundamentals caching layer.

fetch_fundamentals() in main.py hits yfinance's tk.info once per
symbol — PE/ROE/sector/debt-equity/etc rarely move within a week.
This module caches that dict in a "Fundamentals Cache" Sheets tab
(Symbol | FetchDate | DataJSON) and only refetches when the cached
entry is older than FUNDAMENTALS_CACHE_DAYS. Price, RSI, technicals
still refetch every run — untouched, unaffected by this module.

Reads the whole cache tab once per pipeline run (1 API call), not
once per symbol, so this doesn't trade one bottleneck for another.
"""
import json
import logging
import math
from datetime import datetime, date

log = logging.getLogger(__name__)

CACHE_TAB = "Fundamentals Cache"


def _sanitize(data):
    """Drop NaN/Infinity floats before JSON-encoding — same class of
    bug main.py's clean_row() guards against for sheet writes."""
    clean = {}
    for k, v in data.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            clean[k] = None
        else:
            clean[k] = v
    return clean


def load_cache(sh):
    """
    Reads the entire Fundamentals Cache tab in one call.
    Returns {symbol: (fetch_date, data_dict)}. Creates the tab if missing.
    """
    try:
        ws = sh.worksheet(CACHE_TAB)
    except Exception:
        ws = sh.add_worksheet(CACHE_TAB, rows=500, cols=3)
        ws.append_row(["Symbol", "FetchDate", "DataJSON"])
        return {}

    rows = ws.get_all_values()[1:]
    cache = {}
    for row in rows:
        if not row or not row[0]:
            continue
        sym = row[0].strip().upper()
        try:
            fdate = datetime.strptime(row[1], "%Y-%m-%d").date()
            data = json.loads(row[2])
        except (ValueError, IndexError, json.JSONDecodeError):
            continue
        cache[sym] = (fdate, data)
    return cache


def get_or_fetch_fundamentals(sym, cache, max_age_days=7):
    """
    Returns a fundamentals dict for `sym`. Uses `cache` if a
    fresh-enough entry exists; otherwise calls main.fetch_fundamentals()
    and updates `cache` in place. Caller persists `cache` via
    save_cache() once per run, not once per symbol.
    """
    from main import fetch_fundamentals  # lazy import — avoids circular import with main.py

    sym = sym.upper()
    today = date.today()

    entry = cache.get(sym)
    if entry:
        fdate, data = entry
        if (today - fdate).days < max_age_days and data:
            return data

    data = fetch_fundamentals(sym)
    if data:
        cache[sym] = (today, _sanitize(data))
    return data


def save_cache(sh, cache):
    """Overwrites the Fundamentals Cache tab with current in-memory
    cache. Called once per run, not per symbol."""
    try:
        ws = sh.worksheet(CACHE_TAB)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(CACHE_TAB, rows=500, cols=3)

    ws.append_row(["Symbol", "FetchDate", "DataJSON"])
    rows = [
        [sym, fdate.strftime("%Y-%m-%d"), json.dumps(data)]
        for sym, (fdate, data) in cache.items()
    ]
    if rows:
        ws.append_rows(rows)
    log.info(f"Fundamentals Cache: {len(rows)} symbols persisted")
