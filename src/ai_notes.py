"""
Full-text Strengths/Weaknesses storage.

GITHUB DATA (and Growth Screener, and watchlist tabs) now store only
a short strengths/weaknesses count in-row to keep those sheets light
and fast to render. Full pipe-joined explanation text is appended
here instead — one row per symbol per run, looked up by Symbol + Date.
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

NOTES_TAB = "AI Notes"


def append_notes(sh, note_rows):
    """
    note_rows: list of [symbol, strengths_str, weaknesses_str] collected
    during a run. Appends one row per symbol, tagged with today's date,
    in a single batch call — not one call per symbol.
    """
    try:
        ws = sh.worksheet(NOTES_TAB)
    except Exception:
        ws = sh.add_worksheet(NOTES_TAB, rows=5000, cols=4)
        ws.append_row(["Date", "Symbol", "Strengths", "Weaknesses"])

    today = datetime.now().strftime("%Y-%m-%d")
    rows = [[today, sym, strengths, weaknesses] for sym, strengths, weaknesses in note_rows]
    if rows:
        ws.append_rows(rows)
    log.info(f"AI Notes: {len(rows)} entries appended for {today}")
