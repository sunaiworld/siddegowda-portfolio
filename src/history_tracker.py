"""
Historical snapshot tracker.

GITHUB DATA and Future Buy tabs are ws.clear()'d and rewritten every
run — yesterday's score/PE/RSI is gone by the next run. This module
appends (never clears) one row per symbol per run to a "History" tab,
and one summary row per run to a "Portfolio History" tab, so score
trend / PE trend / RSI trend become queryable later without any new
API calls — it reuses rows main.py already built in run_portfolio_update().
"""
import logging
from datetime import datetime
from main import GITHUB_DATA_COLS

log = logging.getLogger(__name__)

HISTORY_TAB = "History"
PORTFOLIO_HISTORY_TAB = "Portfolio History"


def _get_or_create(sh, tab_name, headers):
    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(tab_name, rows=5000, cols=len(headers))
        ws.append_row(headers)
    return ws


def append_history_snapshot(sh, results, portfolio_live_value):
    """
    results: the same row-lists build_result_row()/write_github_data()
    already use, indexed via main.GITHUB_DATA_COLS so this stays
    correct if the row layout shifts again (like the Day Chg% bug).
    Call once per run, after `results` is finalized.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    ws = _get_or_create(sh, HISTORY_TAB,
                         ["Date", "Symbol", "CMP", "PE", "RSI", "Total Score", "Final Action"])

    snap_rows = []
    for row in results:
        try:
            sym    = row[GITHUB_DATA_COLS["symbol"]]
            cmp    = row[GITHUB_DATA_COLS["cmp"]]
            pe     = row[GITHUB_DATA_COLS["pe"]]
            rsi    = row[GITHUB_DATA_COLS["rsi"]]
            total  = row[GITHUB_DATA_COLS["total"]]
            action = row[GITHUB_DATA_COLS["action"]]
        except (IndexError, KeyError):
            continue
        snap_rows.append([today, sym, cmp, pe, rsi, total, action])

    if snap_rows:
        ws.append_rows(snap_rows)
    log.info(f"History: {len(snap_rows)} symbol snapshots appended for {today}")

    pws = _get_or_create(sh, PORTFOLIO_HISTORY_TAB, ["Date", "Portfolio Value"])
    pws.append_row([today, round(portfolio_live_value, 2)])
    log.info(f"Portfolio History: value snapshot appended for {today}")
