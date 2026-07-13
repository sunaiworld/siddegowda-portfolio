"""
Two responsibilities, both backed by the same Google Sheet (no new
infra):

1. Bot Update offset persistence — GitHub Actions has no disk between
   runs, so the Telegram getUpdates() offset is stored in a small
   "Bot State" tab (cell A1) instead of a local file.
2. Cached-row reader — /portfolio, /buy, /sell, /top read the
   existing "GITHUB DATA" tab (already written by run_portfolio_update)
   instead of hitting yfinance again.
"""
import logging
from main import get_gspread_client, SHEET_ID, GITHUB_DATA_COLS

log = logging.getLogger(__name__)

STATE_TAB = "Bot State"


def _get_sheet():
    gc = get_gspread_client()
    return gc.open_by_key(SHEET_ID)


def get_offset():
    """Returns last processed Telegram update_id + 1, or 0 if none stored."""
    sh = _get_sheet()
    try:
        ws = sh.worksheet(STATE_TAB)
    except Exception:
        ws = sh.add_worksheet(STATE_TAB, rows=2, cols=2)
        ws.update("A1", "0")
        return 0
    val = ws.acell("A1").value
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def set_offset(update_id):
    sh = _get_sheet()
    ws = sh.worksheet(STATE_TAB)
    ws.update("A1", str(update_id))


def read_cached_rows():
    """
    Returns list-of-dict rows from the GITHUB DATA tab, keyed by
    GITHUB_DATA_COLS. Empty list if tab missing or empty.
    """
    sh = _get_sheet()
    try:
        ws = sh.worksheet("GITHUB DATA")
    except Exception:
        log.warning("GITHUB DATA tab not found")
        return []

    raw = ws.get_all_values()[1:]  # skip header
    out = []
    for row in raw:
        if not row or not row[0]:
            continue
        d = {}
        for key, idx in GITHUB_DATA_COLS.items():
            d[key] = row[idx] if idx < len(row) else ""
        out.append(d)
    return out


def last_updated(rows):
    for r in rows:
        if r.get("updated"):
            return r["updated"]
    return "unknown"