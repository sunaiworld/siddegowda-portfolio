"""
Raw + summarized news cache, mirroring fund_cache.py's shape
(load once per run, save once per run) but WITHOUT ws.clear() —
per the project's standing rule, destructive ops used elsewhere in
the codebase (fund_cache.save_cache does clear()+rewrite) are not
copied into new code without explicit approval. This does a targeted
upsert instead: existing rows are updated in place by range, new
symbols are appended.
"""
import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CACHE_TAB = "News Cache"
FRESHNESS_HOURS = 6


def load_cache(sh):
    """Returns (cache, ws, existing_symbol_rows):
    cache = {symbol: (fetch_dt, news_result_dict, raw_articles_list)}
    existing_symbol_rows = {symbol: sheet_row_number} — built from the
    same read, so callers no longer need a second get_all_values() call
    to know where each symbol's row is."""
    try:
        ws = sh.worksheet(CACHE_TAB)
    except Exception:
        ws = sh.add_worksheet(CACHE_TAB, rows=500, cols=4)
        ws.append_row(["Symbol", "FetchTimestamp", "NewsResultJSON", "RawArticlesJSON"])
        return {}, ws, {}

    rows = ws.get_all_values()[1:]
    cache = {}
    existing_symbol_rows = {}
    for idx, row in enumerate(rows):
        if not row or not row[0]:
            continue
        sym = row[0].strip().upper()
        existing_symbol_rows[sym] = idx + 2  # +2: 1-indexed sheet rows + header row
        try:
            fdt = datetime.fromisoformat(row[1])
            result_dict = json.loads(row[2]) if row[2] else {}
            raw_articles = json.loads(row[3]) if len(row) > 3 and row[3] else []
        except (ValueError, IndexError, json.JSONDecodeError):
            continue
        cache[sym] = (fdt, result_dict, raw_articles)
    return cache, ws, existing_symbol_rows


def is_fresh(fetch_dt, max_age_hours=FRESHNESS_HOURS):
    age = datetime.now(timezone.utc) - fetch_dt
    return age.total_seconds() < max_age_hours * 3600


def upsert(ws, symbol, news_result, raw_articles, existing_symbol_rows):
    """
    Targeted update: if symbol already has a row, update just that row's
    range. If new, append. No clear(), no full-sheet rewrite.
    existing_symbol_rows: {symbol: row_number} built once by the caller
    from the current sheet state, avoiding a lookup per symbol.
    """
    row_values = [
        symbol,
        datetime.now(timezone.utc).isoformat(),
        json.dumps(news_result.__dict__),
        json.dumps(raw_articles),
    ]
    row_num = existing_symbol_rows.get(symbol)
    if row_num:
        ws.update(f"A{row_num}:D{row_num}", [row_values])
    else:
        # Header occupies row 1; existing_symbol_rows holds exactly the
        # symbols already on the sheet, so the next append lands at
        # (count of known symbols + 2) — no extra read needed to know this.
        next_row = len(existing_symbol_rows) + 2
        ws.append_row(row_values)
        existing_symbol_rows[symbol] = next_row
