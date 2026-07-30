"""
news_cache.py — News Engine: Google Sheets Cache Layer
Persists per-symbol raw articles + AI digest inside a dedicated
"News Cache" worksheet.  Hardened against the Sheets 50,000-character
cell limit and consecutive write quota (429) errors.

Sheet layout (one row per symbol, no header):
  col A  symbol          (lookup key)
  col B  last_fetched    (ISO-8601 UTC timestamp)
  col C  digest          (AI summary text, plain string)
  col D  raw_articles    (JSON — MUST stay below RAW_ARTICLES_CELL_LIMIT)
"""

import json
import logging
import time

import gspread
import gspread.exceptions

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
TAB_NAME              = "News Cache"
RAW_ARTICLES_CELL_LIMIT = 45_000   # Google Sheets hard limit is 50 000; keep 5 k headroom

# Column indices (0-based inside the row list we read/write)
_COL_SYMBOL       = 0
_COL_LAST_FETCHED = 1
_COL_DIGEST       = 2
_COL_RAW_ARTICLES = 3
_COL_BULLISH      = 4
_COL_BEARISH      = 5
_COL_SENTIMENT    = 6
_COL_REASON       = 7
_COL_SOURCE       = 8
_NUM_COLS         = 9


# ── Internal helpers ─────────────────────────────────────────────────────────

def _get_or_create_worksheet(sh):
    """Return the News Cache worksheet, creating it if absent."""
    try:
        return sh.worksheet(TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(TAB_NAME, rows=500, cols=_NUM_COLS)
        log.info(f"[news_cache] Created new worksheet: {TAB_NAME!r}")
        return ws


def _truncate_raw_articles(raw_articles: list, limit: int = RAW_ARTICLES_CELL_LIMIT) -> str:
    """
    Serialise *raw_articles* to JSON, then truncate articles one-by-one
    from the end until the serialised string fits within *limit* characters.

    Logs a warning whenever truncation is necessary so the operator knows
    data was dropped.

    Returns a JSON string guaranteed to be ≤ limit characters.
    """
    if not raw_articles:
        return "[]"

    # Happy path — no truncation needed.
    serialised = json.dumps(raw_articles, ensure_ascii=False)
    if len(serialised) <= limit:
        return serialised

    # Truncate from the tail until we fit.
    original_count = len(raw_articles)
    articles = list(raw_articles)  # shallow copy so we don't mutate caller's list

    while articles:
        articles.pop()
        serialised = json.dumps(articles, ensure_ascii=False)
        if len(serialised) <= limit:
            dropped = original_count - len(articles)
            log.warning(
                f"[news_cache] RAW_ARTICLES truncated: dropped {dropped} of "
                f"{original_count} articles to stay within {limit}-char cell limit "
                f"(retained {len(articles)}, serialised size {len(serialised)} chars)."
            )
            return serialised

    # Edge case: even a single article exceeds the limit — store empty array.
    log.warning(
        f"[news_cache] RAW_ARTICLES: all {original_count} articles exceeded the "
        f"{limit}-char cell limit individually; storing empty array."
    )
    return "[]"


# ── Public API ───────────────────────────────────────────────────────────────

def load(sh):
    """
    Read the full News Cache worksheet. Returns (cache, row_map, ws):
      cache    — {symbol: {...}} same as before
      row_map  — {symbol: sheet_row_number} built from this same read,
                 so flush() never has to call ws.find() per symbol
      ws       — the worksheet object, so callers never re-resolve it
    """
    ws    = _get_or_create_worksheet(sh)
    rows  = ws.get_all_values()
    cache = {}
    row_map = {}

    for i, row in enumerate(rows):
        if not row or not row[_COL_SYMBOL].strip():
            continue
        sym = row[_COL_SYMBOL].strip().upper()
        row_map[sym] = i + 1   # sheet rows are 1-indexed

        try:
            raw_json = row[_COL_RAW_ARTICLES] if len(row) > _COL_RAW_ARTICLES else "[]"
            raw      = json.loads(raw_json) if raw_json.strip() else []
        except (json.JSONDecodeError, ValueError):
            raw = []

        digest_raw = row[_COL_DIGEST] if len(row) > _COL_DIGEST else ""

        try:
            parsed = json.loads(digest_raw) if digest_raw.strip() else None
        except (json.JSONDecodeError, ValueError):
            parsed = None

        if isinstance(parsed, dict):
            cache[sym] = {
                "last_fetched":  row[_COL_LAST_FETCHED] if len(row) > _COL_LAST_FETCHED else "",
                "digest":        parsed.get("summary", ""),
                "bullish_score": parsed.get("bullish_score", row[_COL_BULLISH] if len(row) > _COL_BULLISH else ""),
                "bearish_score": parsed.get("bearish_score", row[_COL_BEARISH] if len(row) > _COL_BEARISH else ""),
                "sentiment":     parsed.get("sentiment", row[_COL_SENTIMENT] if len(row) > _COL_SENTIMENT else ""),
                "reason":        parsed.get("reason", row[_COL_REASON] if len(row) > _COL_REASON else ""),
                "source":        parsed.get("source", row[_COL_SOURCE] if len(row) > _COL_SOURCE else ""),
                "raw_articles":  raw,
            }
        else:
            cache[sym] = {
                "last_fetched":  row[_COL_LAST_FETCHED] if len(row) > _COL_LAST_FETCHED else "",
                "digest":        digest_raw,
                "bullish_score": row[_COL_BULLISH]   if len(row) > _COL_BULLISH   else "",
                "bearish_score": row[_COL_BEARISH]   if len(row) > _COL_BEARISH   else "",
                "sentiment":     row[_COL_SENTIMENT] if len(row) > _COL_SENTIMENT else "",
                "reason":        row[_COL_REASON]    if len(row) > _COL_REASON    else "",
                "source":        row[_COL_SOURCE]    if len(row) > _COL_SOURCE    else "",
                "raw_articles":  raw,
            }

    log.info(f"[news_cache] Loaded {len(cache)} cached symbols from {TAB_NAME!r}")
    return cache, row_map, ws

def stage_upsert(pending: dict, symbol: str, last_fetched: str, digest: str, raw_articles: list,
                  bullish_score: float = 0.0, bearish_score: float = 0.0,
                  sentiment: str = "", reason: str = "", source: str = "") -> None:
    """No API call — stashes the row in `pending` for a single flush() later."""
    symbol = symbol.strip().upper()
    raw_json = _truncate_raw_articles(raw_articles, RAW_ARTICLES_CELL_LIMIT)
    pending[symbol] = [symbol, last_fetched, digest, raw_json,
                        str(bullish_score), str(bearish_score), sentiment, reason, source]


def flush(ws, row_map: dict, pending: dict) -> None:
    """Writes everything staged via stage_upsert() in a handful of batched calls."""
    if not pending:
        return
    updates, new_rows = [], []
    for sym, row in pending.items():
        if sym in row_map:
            r = row_map[sym]
            updates.append({"range": f"A{r}:I{r}", "values": [row]})
        else:
            new_rows.append(row)

    if updates:
        for i in range(0, len(updates), 20):
            ws.batch_update(updates[i:i + 20], value_input_option="RAW")
            time.sleep(1.5)
    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")
    log.info(f"[news_cache] Flushed {len(updates)} updates, {len(new_rows)} new rows")


def upsert(sh, symbol: str, last_fetched: str, digest: str, raw_articles: list, bullish_score: float = 0.0, bearish_score: float = 0.0, sentiment: str = "", reason: str = "", source: str = "") -> None:
    """
    Insert or update the cache row for *symbol*.

    Enforces the RAW_ARTICLES_CELL_LIMIT before writing so the Sheets
    50,000-character per-cell quota is never breached.

    Raises gspread.exceptions.APIError on unrecoverable Sheets errors
    (callers are responsible for spacing writes apart to avoid 429s).
    """
    symbol = symbol.strip().upper()
    ws     = _get_or_create_worksheet(sh)

    # Guard: serialise raw_articles with the cell-limit helper.
    raw_json = _truncate_raw_articles(raw_articles, RAW_ARTICLES_CELL_LIMIT)

    new_row = [""] * _NUM_COLS
    new_row[_COL_SYMBOL]       = symbol
    new_row[_COL_LAST_FETCHED] = last_fetched
    new_row[_COL_DIGEST]       = digest
    new_row[_COL_RAW_ARTICLES] = raw_json
    new_row[_COL_BULLISH]      = str(bullish_score)
    new_row[_COL_BEARISH]      = str(bearish_score)
    new_row[_COL_SENTIMENT]    = sentiment
    new_row[_COL_REASON]       = reason
    new_row[_COL_SOURCE]       = source

    # Find existing row for this symbol (column A, 1-indexed).
    cell = ws.find(symbol, in_column=1)

    if cell:
        row_num = cell.row
        ws.update(f"A{row_num}:D{row_num}", [new_row])
        log.info(f"[news_cache] Updated row {row_num} for symbol {symbol!r}")
    else:
        ws.append_row(new_row, value_input_option="RAW")
        log.info(f"[news_cache] Appended new row for symbol {symbol!r}")
