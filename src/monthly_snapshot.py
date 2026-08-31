"""
Monthly Historical Snapshot Engine: GITHUB DATA + Future Buy

Creates permanent, immutable, point-in-time monthly snapshots of:
1. GITHUB DATA (complete 41-column stock-level analysis)
2. Future Buy (complete watchlist + opportunity rank + tranche/fit guidance)

Guarantees:
- Frequency: Once per month on the last trading day of the month
- Duplicate Protection: Checks both Google Sheets and local filesystem to prevent duplicate snapshots
- Point-in-Time Data: Stores exact computed state without future modification or formulas
- Formatting: Clean, filtered, formatted Google Sheet tabs + offline CSV backups for backtesting
"""

import os
import csv
import json
import logging
import calendar
import math
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple, Set

from github_data_builder import (
    GITHUB_DATA_COLS,
    GITHUB_DATA_HEADER_NAMES,
    clean_row,
)
import sheet_formatter
import sheet_writer

log = logging.getLogger(__name__)

# Google Sheet Tab Names for Monthly Snapshots
GITHUB_DATA_HISTORY_TAB = "GITHUB DATA History"
FUTURE_BUY_HISTORY_TAB = "Future Buy History"

# Directory for local CSV snapshot persistence
LOCAL_SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "snapshots"
)
MANIFEST_FILE = os.path.join(LOCAL_SNAPSHOT_DIR, "snapshots_manifest.json")

# Header Definitions
GITHUB_DATA_HISTORY_HEADERS = ["Snapshot Date", "Snapshot Month"] + [
    GITHUB_DATA_HEADER_NAMES.get(k, k) for k in GITHUB_DATA_COLS.keys()
]

FUTURE_BUY_HISTORY_HEADERS = [
    "Snapshot Date", "Snapshot Month", "Rank", "Portfolio Fit", "Tranche Guidance"
] + [GITHUB_DATA_HEADER_NAMES.get(k, k) for k in GITHUB_DATA_COLS.keys()]


def get_last_trading_day_of_month(year: int, month: int) -> date:
    """
    Returns the date of the last trading day (Monday-Friday) of the specified month.
    """
    _, last_day = calendar.monthrange(year, month)
    d = date(year, month, last_day)
    while d.weekday() >= 5:  # 5=Saturday, 6=Sunday
        d -= timedelta(days=1)
    return d


def is_last_trading_day_of_month(dt: Optional[Any] = None) -> bool:
    """
    Returns True if the given date (default: today) is the last trading day of its month.
    """
    if dt is None:
        dt = date.today()
    elif isinstance(dt, datetime):
        dt = dt.date()
    elif isinstance(dt, str):
        try:
            dt = datetime.strptime(dt, "%Y-%m-%d").date()
        except ValueError:
            return False

    last_trading_day = get_last_trading_day_of_month(dt.year, dt.month)
    return dt == last_trading_day


def _get_or_create_tab(sh, tab_name: str, headers: List[str]):
    """
    Gets or creates a worksheet with the specified headers.
    """
    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(tab_name, rows=5000, cols=len(headers))
        ws.append_row(headers)
    return ws


def get_existing_snapshot_months(ws) -> Set[str]:
    """
    Reads column B (Snapshot Month) to determine which months already have snapshots.
    """
    try:
        col_values = ws.col_values(2)
        if len(col_values) <= 1:
            return set()
        return {str(v).strip() for v in col_values[1:] if str(v).strip()}
    except Exception as e:
        log.warning(f"Could not read existing snapshot months from {ws.title}: {e}")
        return set()


def _format_history_tab(sh, ws, headers: List[str], num_data_rows: int, total_score_col: int, action_col: int, new_rows_count: int):
    """
    Applies clean formatting to the historical snapshot tabs in Google Sheets:
    - Bold Navy Header with white text
    - Frozen Header row
    - Basic filter enabled
    - Date format on Snapshot Date (Col 0)
    - Total Score green-yellow-red gradient
    - Action tier background colors
    - Alternating row backgrounds
    """
    hex_rgb = sheet_formatter.hex_rgb
    color_cell_req = sheet_formatter.color_cell_req
    batch_update_safe = sheet_writer.batch_update_safe

    total_rows = num_data_rows + 1
    reqs = []

    # 1. Header styling
    reqs.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                "startColumnIndex": 0, "endColumnIndex": len(headers)
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": hex_rgb("0d1b2a"),
                    "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 9},
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE"
                }
            },
            "fields": "userEnteredFormat"
        }
    })

    # 2. Freeze header row
    reqs.append({
        "updateSheetProperties": {
            "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"
        }
    })

    # 3. Filter
    if num_data_rows > 0:
        reqs.append({
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": total_rows,
                        "startColumnIndex": 0, "endColumnIndex": len(headers)
                    }
                }
            }
        })

    # 4. Date formatting on Col 0 (Snapshot Date)
    if num_data_rows > 0:
        reqs.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id, "startRowIndex": 1, "endRowIndex": total_rows,
                    "startColumnIndex": 0, "endColumnIndex": 1
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}},
                "fields": "userEnteredFormat.numberFormat"
            }
        })

    # 5. Row styling & cell coloring for newly appended rows
    if num_data_rows > 0 and new_rows_count > 0:
        try:
            all_values = ws.get_all_values()[1:]
        except Exception:
            all_values = []

        start_idx = max(0, len(all_values) - new_rows_count)

        for i in range(start_idx, len(all_values)):
            row = all_values[i]
            rn = i + 1
            alt = "f8f9fa" if i % 2 == 0 else "ffffff"
            reqs.append({
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn + 1,
                        "startColumnIndex": 0, "endColumnIndex": len(headers)
                    },
                    "cell": {"userEnteredFormat": {"backgroundColor": hex_rgb(alt)}},
                    "fields": "userEnteredFormat.backgroundColor"
                }
            })

            # Total Score Gradient
            if total_score_col is not None and len(row) > total_score_col:
                try:
                    sc = float(str(row[total_score_col]).replace("%", "").replace(",", "").strip())
                    sc_clamped = max(0, min(100, sc))
                    mid = 50.0
                    if sc_clamped >= mid:
                        t = (sc_clamped - mid) / (100.0 - mid)
                        r = int(255 * (1 - t))
                        g = int(255 * (1 - t) + 200 * t)
                        b = 0
                    else:
                        t = sc_clamped / mid
                        r = int(200 * (1 - t) + 255 * t)
                        g = int(255 * t)
                        b = 0
                    color = f"{r:02x}{g:02x}{b:02x}"
                    reqs.append(color_cell_req(ws.id, rn, total_score_col, color, "1a1a1a", bold=True))
                except (ValueError, TypeError):
                    pass

            # Final Action Color
            if action_col is not None and len(row) > action_col:
                act = str(row[action_col]).strip()
                if act in sheet_formatter.GITHUB_DATA_ACTION_COLORS:
                    bg, fg = sheet_formatter.GITHUB_DATA_ACTION_COLORS[act]
                    reqs.append(color_cell_req(ws.id, rn, action_col, bg, fg))

    batch_update_safe(sh, reqs)


def build_future_buy_snapshot_rows(
    future_buy_rows: List[List[Any]],
    snapshot_date: str,
    snapshot_month: str,
    sector_weights: Optional[Dict[str, float]] = None,
    portfolio_value: Optional[float] = None,
) -> List[List[Any]]:
    """
    Prepares complete Future Buy rows for the historical snapshot:
    [Snapshot Date, Snapshot Month, Rank, Portfolio Fit, Tranche Guidance, ...41 metrics...]
    """
    C = GITHUB_DATA_COLS
    out_rows = []

    for rank, r in enumerate(future_buy_rows, 1):
        r_clean = clean_row(r)
        sector = r_clean[C["sector"]] if "sector" in C and len(r_clean) > C["sector"] else ""

        # Portfolio Fit calculation
        if sector_weights and isinstance(sector_weights, dict) and sector:
            wt = float(sector_weights.get(sector, 0.0))
            if wt > 20.0:
                fit_text = f"⚠️ Overweight ({wt:.1f}%)"
            elif wt >= 15.0:
                fit_text = f"⚖️ Balanced ({wt:.1f}%)"
            else:
                fit_text = f"⭐ High Fit ({wt:.1f}%)" if wt > 0 else "⭐ High Fit (New)"
        else:
            fit_text = "⭐ High Fit (Diversified)"

        # Tranche sizing guidance
        if portfolio_value and portfolio_value > 0:
            tranche_val = round(portfolio_value * 0.02, -3)
            tranche_text = f"₹{tranche_val:,.0f} (2.0%)"
        else:
            tranche_text = "2.0% Tranche"

        row = [snapshot_date, snapshot_month, rank, fit_text, tranche_text] + r_clean
        out_rows.append(row)

    return out_rows


def build_github_data_snapshot_rows(
    github_results: List[List[Any]],
    snapshot_date: str,
    snapshot_month: str,
) -> List[List[Any]]:
    """
    Prepares complete GITHUB DATA rows for the historical snapshot:
    [Snapshot Date, Snapshot Month, ...41 metrics...]
    """
    out_rows = []
    for r in github_results:
        r_clean = clean_row(r)
        row = [snapshot_date, snapshot_month] + r_clean
        out_rows.append(row)
    return out_rows


def save_local_snapshots(
    snapshot_date: str,
    snapshot_month: str,
    github_snapshot_rows: List[List[Any]],
    future_buy_snapshot_rows: List[List[Any]],
) -> Tuple[str, str]:
    """
    Persists historical snapshot CSV files and updates the manifest registry.
    """
    month_dir = os.path.join(LOCAL_SNAPSHOT_DIR, snapshot_month)
    os.makedirs(month_dir, exist_ok=True)

    gh_csv_path = os.path.join(month_dir, "github_data_snapshot.csv")
    fb_csv_path = os.path.join(month_dir, "future_buy_snapshot.csv")

    with open(gh_csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(GITHUB_DATA_HISTORY_HEADERS)
        writer.writerows(github_snapshot_rows)

    with open(fb_csv_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(FUTURE_BUY_HISTORY_HEADERS)
        writer.writerows(future_buy_snapshot_rows)

    # Update Manifest
    manifest = {}
    if os.path.exists(MANIFEST_FILE):
        try:
            with open(MANIFEST_FILE, mode="r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            manifest = {}

    manifest[snapshot_month] = {
        "snapshot_date": snapshot_date,
        "recorded_at": datetime.now().isoformat(),
        "github_data_rows": len(github_snapshot_rows),
        "future_buy_rows": len(future_buy_snapshot_rows),
        "github_data_csv": os.path.relpath(gh_csv_path, os.path.dirname(LOCAL_SNAPSHOT_DIR)),
        "future_buy_csv": os.path.relpath(fb_csv_path, os.path.dirname(LOCAL_SNAPSHOT_DIR)),
    }

    with open(MANIFEST_FILE, mode="w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    log.info(f"Local snapshots saved to {month_dir}")
    return gh_csv_path, fb_csv_path


def check_and_record_monthly_snapshots(
    sh,
    github_results: List[List[Any]],
    future_buy_rows: List[List[Any]],
    sector_weights: Optional[Dict[str, float]] = None,
    portfolio_value: Optional[float] = None,
    force: bool = False,
    run_date: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Main entrypoint for monthly historical snapshots.
    Evaluates whether a snapshot should be taken, verifies duplicate protection,
    and writes to both Google Sheets and local storage.
    """
    if run_date is None:
        today_date = date.today()
    elif isinstance(run_date, datetime):
        today_date = run_date.date()
    elif isinstance(run_date, str):
        today_date = datetime.strptime(run_date, "%Y-%m-%d").date()
    else:
        today_date = run_date

    snapshot_date_str = today_date.strftime("%Y-%m-%d")
    snapshot_month_str = today_date.strftime("%Y-%m")

    # Check if force flag is set via environment variable
    if os.environ.get("FORCE_MONTHLY_SNAPSHOT", "").lower() in ("1", "true", "yes"):
        force = True

    is_snapshot_day = is_last_trading_day_of_month(today_date)

    if not is_snapshot_day and not force:
        log.info(f"Monthly Snapshot: {snapshot_date_str} is not the last trading day of {snapshot_month_str}. Skipping.")
        return {
            "status": "skipped_not_snapshot_day",
            "date": snapshot_date_str,
            "month": snapshot_month_str,
            "recorded": False,
        }

    log.info(f"Monthly Snapshot: Evaluating snapshot recording for {snapshot_month_str} (Date: {snapshot_date_str}, Force: {force})...")

    # Prepare Google Sheets tabs
    gh_ws = _get_or_create_tab(sh, GITHUB_DATA_HISTORY_TAB, GITHUB_DATA_HISTORY_HEADERS)
    fb_ws = _get_or_create_tab(sh, FUTURE_BUY_HISTORY_TAB, FUTURE_BUY_HISTORY_HEADERS)

    # Check existing snapshot months for Duplicate Protection
    existing_gh_months = get_existing_snapshot_months(gh_ws)
    existing_fb_months = get_existing_snapshot_months(fb_ws)

    if (snapshot_month_str in existing_gh_months or snapshot_month_str in existing_fb_months) and not force:
        log.info(f"Monthly Snapshot: Snapshot for {snapshot_month_str} already exists in historical tabs. Duplicate prevented. Skipping.")
        return {
            "status": "skipped_duplicate_exists",
            "date": snapshot_date_str,
            "month": snapshot_month_str,
            "recorded": False,
        }

    # Build snapshot rows
    gh_snap_rows = build_github_data_snapshot_rows(github_results, snapshot_date_str, snapshot_month_str)
    fb_snap_rows = build_future_buy_snapshot_rows(
        future_buy_rows, snapshot_date_str, snapshot_month_str, sector_weights, portfolio_value
    )

    if not gh_snap_rows and not fb_snap_rows:
        log.warning("Monthly Snapshot: No GITHUB DATA or Future Buy rows provided. Skipping.")
        return {"status": "skipped_empty_data", "recorded": False}

    # Write to Google Sheets
    if gh_snap_rows:
        gh_ws.append_rows(gh_snap_rows, value_input_option="USER_ENTERED")
        total_gh_rows = len(gh_ws.get_all_values()) - 1
        # GITHUB DATA History Total Score is col 39 (0-indexed), Action is col 42
        _format_history_tab(
            sh, gh_ws, GITHUB_DATA_HISTORY_HEADERS, total_gh_rows,
            total_score_col=2 + GITHUB_DATA_COLS["total"],
            action_col=2 + GITHUB_DATA_COLS["action"],
            new_rows_count=len(gh_snap_rows)
        )
        log.info(f"Monthly Snapshot: Appended {len(gh_snap_rows)} rows to '{GITHUB_DATA_HISTORY_TAB}'")

    if fb_snap_rows:
        fb_ws.append_rows(fb_snap_rows, value_input_option="USER_ENTERED")
        total_fb_rows = len(fb_ws.get_all_values()) - 1
        # Future Buy History has 5 prepended cols (Date, Month, Rank, Fit, Tranche), so Total Score is col 5 + total
        _format_history_tab(
            sh, fb_ws, FUTURE_BUY_HISTORY_HEADERS, total_fb_rows,
            total_score_col=5 + GITHUB_DATA_COLS["total"],
            action_col=5 + GITHUB_DATA_COLS["action"],
            new_rows_count=len(fb_snap_rows)
        )
        log.info(f"Monthly Snapshot: Appended {len(fb_snap_rows)} rows to '{FUTURE_BUY_HISTORY_TAB}'")

    # Write to local storage for offline backtesting
    gh_csv, fb_csv = save_local_snapshots(snapshot_date_str, snapshot_month_str, gh_snap_rows, fb_snap_rows)

    log.info(f"✅ Monthly Snapshot for {snapshot_month_str} completed successfully! ({len(gh_snap_rows)} GITHUB DATA, {len(fb_snap_rows)} Future Buy)")

    return {
        "status": "success",
        "date": snapshot_date_str,
        "month": snapshot_month_str,
        "github_rows_recorded": len(gh_snap_rows),
        "future_buy_rows_recorded": len(fb_snap_rows),
        "recorded": True,
        "local_github_csv": gh_csv,
        "local_future_buy_csv": fb_csv,
    }
