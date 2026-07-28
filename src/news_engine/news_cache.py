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
_NUM_COLS         = 4


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

def load(sh) -> dict:
    """
    Read the full News Cache worksheet and return a dict keyed by symbol.

    The Digest column may hold either:
      - a JSON object (current format) with keys: summary, bullish_score,
        bearish_score, sentiment, reason, timestamp, source
      - a plain-text string (legacy rows written before this format existed)

    Each returned value is a dict with keys: last_fetched, digest,
    bullish_score, bearish_score, sentiment, reason, source, raw_articles.
    raw_articles is a Python list (parsed from JSON). The Digest cell itself
    is never rewritten here — only the in-memory dict this function returns
    is restructured.
    """
    ws    = _get_or_create_worksheet(sh)
    rows  = ws.get_all_values()
    cache = {}

    for row in rows:
        if not row or not row[_COL_SYMBOL].strip():
            continue
        sym = row[_COL_SYMBOL].strip().upper()
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
            # Digest cell holds the JSON object — unpack its fields.
            cache[sym] = {
                "last_fetched":  row[_COL_LAST_FETCHED] if len(row) > _COL_LAST_FETCHED else "",
                "digest":        parsed.get("summary", ""),
                "bullish_score": parsed.get("bullish_score", ""),
                "bearish_score": parsed.get("bearish_score", ""),
                "sentiment":     parsed.get("sentiment", ""),
                "reason":        parsed.get("reason", ""),
                "source":        parsed.get("source", ""),
                "raw_articles":  raw,
            }
        else:
            # Not JSON (legacy plain-text row) — backward compatible fallback.
            cache[sym] = {
                "last_fetched":  row[_COL_LAST_FETCHED] if len(row) > _COL_LAST_FETCHED else "",
                "digest":        digest_raw,
                "bullish_score": "",
                "bearish_score": "",
                "sentiment":     "",
                "reason":        "",
                "source":        "",
                "raw_articles":  raw,
            }

    log.info(f"[news_cache] Loaded {len(cache)} cached symbols from {TAB_NAME!r}")
    return cache


def upsert(sh, symbol: str, last_fetched: str, digest: str, raw_articles: list) -> None:
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

    # Find existing row for this symbol (column A, 1-indexed).
    cell = ws.find(symbol, in_column=1)

    if cell:
        row_num = cell.row
        ws.update(f"A{row_num}:D{row_num}", [new_row])
        log.info(f"[news_cache] Updated row {row_num} for symbol {symbol!r}")
    else:
        ws.append_row(new_row, value_input_option="RAW")
        log.info(f"[news_cache] Appended new row for symbol {symbol!r}")
