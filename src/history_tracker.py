"""
Historical snapshot tracker + Today's Changes comparison + formatting.

GITHUB DATA and Future Buy tabs are ws.clear()'d and rewritten every
run — yesterday's score/PE/RSI is gone by the next run. This module
appends (never clears) one row per symbol per run to a "History" tab,
and one summary row per run to a "Portfolio History" tab.

Formatting is presentation-only: bold header, frozen row, filter,
alternating rows, number/date formats, a green-yellow-red gradient on
absolute Total Score / Health Score, and Final Action color coding.
No row-to-row comparison, no deltas — Today's Changes and the
Dashboard's health trend already own that analysis; duplicating it
here was explicitly ruled out.
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

HISTORY_TAB = "History"
PORTFOLIO_HISTORY_TAB = "Portfolio History"

HISTORY_HEADERS = ["Date", "Symbol", "CMP", "PE", "RSI", "Total Score",
                    "Final Action", "Quality", "Valuation", "Timing"]
PORTFOLIO_HISTORY_HEADERS = ["Date", "Portfolio Value", "Health Score"]
_H = {name: i for i, name in enumerate(HISTORY_HEADERS)}

BUY_TIER  = {"STRONG BUY", "BUY"}
SELL_TIER = {"AVOID", "SELL"}

MATERIAL_SCORE_DELTA = 5  # |delta| >= this, OR action changed, = material

ACTION_COLORS = {
    "STRONG BUY":  ("00c853", "ffffff"),
    "BUY":         ("0b8043", "ffffff"),
    "ACCUMULATE":  ("d9ead3", "0b8043"),
    "HOLD":        ("fff2cc", "7f4f00"),
    "WATCH":       ("fce8b2", "7f4f00"),
    "AVOID":       ("fde9d9", "c62828"),
    "SELL":        ("cc0000", "ffffff"),
}


def _get_or_create(sh, tab_name, headers):
    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(tab_name, rows=5000, cols=len(headers))
        ws.append_row(headers)
    return ws


def _ensure_header_width(ws, headers):
    """If an existing tab's header row is narrower than `headers`
    (e.g. Portfolio History predates the Health Score column), widen
    just the header row. Never touches data rows."""
    current = ws.row_values(1)
    if len(current) < len(headers):
        ws.update('A1', [headers])


def _to_float(v):
    """Safe float parse — handles Sheets strings, in-memory numbers,
    and empty/missing values uniformly."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _gradient_color(score, lo=0, hi=100):
    """Green (high) -> Yellow (mid) -> Red (low), linear interpolation.
    Presentation only — not a new calculation, just a color mapping
    of the already-stored score."""
    score = _clamp(score, lo, hi)
    mid = (lo + hi) / 2
    if score >= mid:
        t = (score - mid) / (hi - mid) if hi != mid else 1
        r = int(255 * (1 - t))
        g = int(255 * (1 - t) + 200 * t)
        b = 0
    else:
        t = (score - lo) / (mid - lo) if mid != lo else 0
        r = int(200 * (1 - t) + 255 * t)
        g = int(255 * t)
        b = 0
    return f"{r:02x}{g:02x}{b:02x}"


def _format_data_tab(sh, ws, headers, num_data_rows, total_score_col=None, action_col=None, new_rows_count=None):
    """
    Shared presentation-only formatter reused for both History and
    Portfolio History. Reuses sheet_formatter and sheet_writer
    instead of duplicating formatting logic.
    """
    import sheet_formatter
    import sheet_writer

    hex_rgb = sheet_formatter.hex_rgb
    color_cell_req = sheet_formatter.color_cell_req
    batch_update_safe = sheet_writer.batch_update_safe

    total_rows = num_data_rows + 1  # + header
    reqs = []

    # Bold colored header
    reqs.append({"repeatCell": {
        "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1,
                  "startColumnIndex": 0, "endColumnIndex": len(headers)},
        "cell": {"userEnteredFormat": {
            "backgroundColor": hex_rgb("0d1b2a"),
            "textFormat": {"foregroundColor": hex_rgb("ffffff"), "bold": True, "fontSize": 10},
            "verticalAlignment": "MIDDLE"
        }},
        "fields": "userEnteredFormat"
    }})

    # Freeze header row
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": ws.id, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount"
    }})

    # Enable filter over the full data range
    if num_data_rows > 0:
        reqs.append({"setBasicFilter": {"filter": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": total_rows,
                      "startColumnIndex": 0, "endColumnIndex": len(headers)}
        }}})

    # Date formatting — column 0 in both tabs
    if num_data_rows > 0:
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": total_rows,
                      "startColumnIndex": 0, "endColumnIndex": 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "dd-mmm-yyyy"}}},
            "fields": "userEnteredFormat.numberFormat"
        }})

    # Alternating row colors + per-row score gradient / action color
    if num_data_rows > 0 and (total_score_col is not None or action_col is not None) and new_rows_count != 0:
        try:
            all_values = ws.get_all_values()[1:]
        except Exception:
            all_values = []

        start_idx = 0 if new_rows_count is None else max(0, len(all_values) - new_rows_count)

        for i in range(start_idx, len(all_values)):
            row = all_values[i]
            rn = i + 1
            alt = "f8f9fa" if i % 2 == 0 else "ffffff"
            reqs.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": rn, "endRowIndex": rn + 1,
                          "startColumnIndex": 0, "endColumnIndex": len(headers)},
                "cell": {"userEnteredFormat": {"backgroundColor": hex_rgb(alt)}},
                "fields": "userEnteredFormat.backgroundColor"
            }})

            if total_score_col is not None and len(row) > total_score_col:
                score = _to_float(row[total_score_col])
                if score is not None:
                    color = _gradient_color(score)
                    reqs.append(color_cell_req(ws.id, rn, total_score_col, color, "1a1a1a", bold=True))

            if action_col is not None and len(row) > action_col:
                action = row[action_col].strip()
                if action in ACTION_COLORS:
                    bg, fg = ACTION_COLORS[action]
                    reqs.append(color_cell_req(ws.id, rn, action_col, bg, fg))

    # Auto-resize columns
    reqs.append({"autoResizeDimensions": {
        "dimensions": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(headers)}
    }})

    batch_update_safe(sh, reqs)


def _format_history_tab(sh, ws, num_data_rows, new_rows_count=None):
    import sheet_formatter
    import sheet_writer
    batch_update_safe = sheet_writer.batch_update_safe

    reqs = []
    if num_data_rows > 0:
        # CMP — currency
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": num_data_rows + 1,
                      "startColumnIndex": _H["CMP"], "endColumnIndex": _H["CMP"] + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"₹"#,##0.00'}}},
            "fields": "userEnteredFormat.numberFormat"
        }})
        # PE / RSI / Quality / Valuation / Timing — plain numbers
        for col, pattern in [(_H["PE"], "0.00"), (_H["RSI"], "0.0"),
                              (_H["Quality"], "0"), (_H["Valuation"], "0"), (_H["Timing"], "0")]:
            reqs.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": num_data_rows + 1,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat"
            }})
    if reqs:
        batch_update_safe(sh, reqs)

    _format_data_tab(sh, ws, HISTORY_HEADERS, num_data_rows,
                      total_score_col=_H["Total Score"], action_col=_H["Final Action"], new_rows_count=new_rows_count)


def _format_portfolio_history_tab(sh, ws, num_data_rows, new_rows_count=None):
    import sheet_writer
    batch_update_safe = sheet_writer.batch_update_safe

    reqs = []
    if num_data_rows > 0:
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": num_data_rows + 1,
                      "startColumnIndex": 1, "endColumnIndex": 2},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": '"₹"#,##0'}}},
            "fields": "userEnteredFormat.numberFormat"
        }})
        reqs.append({"repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": num_data_rows + 1,
                      "startColumnIndex": 2, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.0"}}},
            "fields": "userEnteredFormat.numberFormat"
        }})
    if reqs:
        batch_update_safe(sh, reqs)

    # Health Score gets the same gradient as Total Score (col index 2)
    _format_data_tab(sh, ws, PORTFOLIO_HISTORY_HEADERS, num_data_rows, total_score_col=2, action_col=None, new_rows_count=new_rows_count)


def append_history_snapshot(sh, results, portfolio_live_value, prices, health_score=None):
    """
    Appends today's analytical snapshot to History and Portfolio History tabs.
    Idempotent: updates today's snapshot in-place if called multiple times on the same date.
    """
    from github_data_builder import GITHUB_DATA_COLS
    import sheet_writer

    today = datetime.now().strftime("%Y-%m-%d")
    ws = _get_or_create(sh, HISTORY_TAB, HISTORY_HEADERS)

    import math
    def clean_val(v):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return ""
        return v

    snap_rows = []
    for row in results:
        try:
            sym       = row[GITHUB_DATA_COLS["symbol"]]
            pe        = clean_val(row[GITHUB_DATA_COLS["pe"]])
            rsi       = clean_val(row[GITHUB_DATA_COLS["rsi"]])
            total     = clean_val(row[GITHUB_DATA_COLS["total"]])
            action    = row[GITHUB_DATA_COLS["action"]]
            quality   = clean_val(row[GITHUB_DATA_COLS["quality"]])
            valuation = clean_val(row[GITHUB_DATA_COLS["valuation"]])
            timing    = clean_val(row[GITHUB_DATA_COLS["timing"]])
        except (IndexError, KeyError):
            continue
        cmp_ = clean_val(prices.get(sym, ""))
        snap_rows.append([today, sym, cmp_, pe, rsi, total, action, quality, valuation, timing])

    if not snap_rows:
        return

    # Check existing rows to prevent same-day duplicates
    existing_history = ws.get_all_values()
    if len(existing_history) > 1:
        non_today_rows = [r for r in existing_history[1:] if r and len(r) > 0 and r[0] != today]
        all_history_data = [HISTORY_HEADERS] + non_today_rows + snap_rows
        sheet_writer.update_sheet_safe(ws, "A1", all_history_data, value_input_option="USER_ENTERED")
        total_history_rows = len(all_history_data) - 1
    else:
        ws.append_rows(snap_rows)
        total_history_rows = len(snap_rows)

    log.info(f"History: {len(snap_rows)} symbol snapshots recorded for {today}")
    _format_history_tab(sh, ws, total_history_rows, len(snap_rows))

    ph_headers = PORTFOLIO_HISTORY_HEADERS
    pws = _get_or_create(sh, PORTFOLIO_HISTORY_TAB, ph_headers)
    _ensure_header_width(pws, ph_headers)

    existing_ph = pws.get_all_values()
    new_ph_entry = [today, round(portfolio_live_value, 2), health_score if health_score is not None else ""]
    if len(existing_ph) > 1:
        non_today_ph = [r for r in existing_ph[1:] if r and len(r) > 0 and r[0] != today]
        all_ph_data = [ph_headers] + non_today_ph + [new_ph_entry]
        sheet_writer.update_sheet_safe(pws, "A1", all_ph_data, value_input_option="USER_ENTERED")
        total_ph_rows = len(all_ph_data) - 1
    else:
        pws.append_row(new_ph_entry)
        total_ph_rows = 1

    log.info(f"Portfolio History: value + health snapshot recorded for {today}")
    _format_portfolio_history_tab(sh, pws, total_ph_rows, 1)


def _load_previous_trading_day_snapshot(sh):
    """
    Reads History once. Returns (prev_date_str, {symbol: row_dict})
    for the most recent date strictly before today.
    """
    try:
        ws = sh.worksheet(HISTORY_TAB)
    except Exception:
        return None, {}

    rows = ws.get_all_values()[1:]
    if not rows:
        return None, {}

    today_str = datetime.now().strftime("%Y-%m-%d")
    prior_dates = {r[_H["Date"]] for r in rows if r and r[_H["Date"]] < today_str}
    if not prior_dates:
        return None, {}

    prev_date = max(prior_dates)

    snapshot = {}
    for r in rows:
        if not r or len(r) <= _H["Timing"] or r[_H["Date"]] != prev_date:
            continue
        sym = r[_H["Symbol"]].strip().upper()
        snapshot[sym] = {
            "cmp": _to_float(r[_H["CMP"]]),
            "pe": _to_float(r[_H["PE"]]),
            "rsi": _to_float(r[_H["RSI"]]),
            "total": _to_float(r[_H["Total Score"]]),
            "action": r[_H["Final Action"]].strip(),
            "quality": _to_float(r[_H["Quality"]]),
            "valuation": _to_float(r[_H["Valuation"]]),
            "timing": _to_float(r[_H["Timing"]]),
        }
    return prev_date, snapshot


def get_previous_health_score(sh):
    """
    Returns (prev_date, prev_health_score) for the most recent PRIOR
    TRADING DAY in Portfolio History. (None, None) if no prior day
    exists yet, or that day predates the Health Score column.
    """
    try:
        ws = sh.worksheet(PORTFOLIO_HISTORY_TAB)
    except Exception:
        return None, None

    rows = ws.get_all_values()[1:]
    if not rows:
        return None, None

    today_str = datetime.now().strftime("%Y-%m-%d")
    prior_rows = [r for r in rows if r and r[0] < today_str]
    if not prior_rows:
        return None, None

    prev_date = max(r[0] for r in prior_rows)
    same_day_rows = [r for r in prior_rows if r[0] == prev_date]
    last_row = same_day_rows[-1]
    health = _to_float(last_row[2]) if len(last_row) > 2 else None
    return prev_date, health


def _dominant_component(dq, dv, dt):
    candidates = [c for c in [("Quality", dq), ("Valuation", dv), ("Timing", dt)] if c[1] is not None]
    if not candidates:
        return None, 0
    dominant = max(candidates, key=lambda c: abs(c[1]))
    if abs(dominant[1]) < 2:
        return None, 0
    return dominant


def _reason_text(dominant, delta, rsi_delta, pe_delta):
    if dominant is None:
        return "No significant driver"
    direction = "improved" if delta > 0 else "weakened"
    detail = ""
    if dominant == "Timing":
        if rsi_delta is not None and rsi_delta < -5:
            detail = " — RSI improving"
        elif rsi_delta is not None and rsi_delta > 5:
            detail = " — RSI weakening"
    elif dominant == "Valuation":
        if pe_delta is not None and pe_delta < 0:
            detail = " — PE cheaper"
        elif pe_delta is not None and pe_delta > 0:
            detail = " — PE more expensive"
    return f"{dominant} {direction}{detail}"


def _tier(action):
    if action in BUY_TIER:
        return "BUY"
    if action in SELL_TIER:
        return "SELL"
    return "OTHER"


def _why_should_i_care(prev_action, today_action, dominant, score_delta):
    prev_tier, today_tier = _tier(prev_action), _tier(today_action)

    if today_tier == "BUY" and prev_tier != "BUY":
        return "Good opportunity to review for additional investment."
    if today_tier == "SELL" and prev_tier != "SELL":
        return "Consider trimming if valuation remains expensive."
    if prev_tier == "BUY" and today_tier != "BUY":
        return "Monitor next quarterly results." if dominant == "Quality" else "Wait before averaging."
    return "Continue holding — thesis strengthening." if score_delta > 0 else "Continue holding — monitor."


def _review_priority(prev_action, today_action, score_delta):
    prev_tier, today_tier = _tier(prev_action), _tier(today_action)
    entered_buy = today_tier == "BUY" and prev_tier != "BUY"
    entered_sell = today_tier == "SELL" and prev_tier != "SELL"

    if entered_buy or entered_sell:
        return "🔴 High"
    if abs(score_delta) >= 8:
        return "🔴 High"
    if abs(score_delta) >= 5:
        return "🟡 Medium"
    return "🟢 Low"


def compute_todays_changes(sh, results):
    """
    Compares today's in-memory `results` against the most recent
    PRIOR TRADING DAY's History snapshot. Call BEFORE
    append_history_snapshot() in the same run.
    """
    from github_data_builder import GITHUB_DATA_COLS

    prev_date, snapshot = _load_previous_trading_day_snapshot(sh)
    if prev_date is None:
        log.info("Today's Changes: no prior trading day in History yet — skipping comparison")
        return {"prev_date": None, "top_improvements": [], "top_deteriorations": [],
                "unchanged_count": len(results), "digest": None}

    changes = []
    unchanged_count = 0

    for row in results:
        try:
            sym          = row[GITHUB_DATA_COLS["symbol"]]
            today_pe     = _to_float(row[GITHUB_DATA_COLS["pe"]])
            today_rsi    = _to_float(row[GITHUB_DATA_COLS["rsi"]])
            today_total  = _to_float(row[GITHUB_DATA_COLS["total"]])
            today_action = row[GITHUB_DATA_COLS["action"]]
            today_q      = _to_float(row[GITHUB_DATA_COLS["quality"]])
            today_v      = _to_float(row[GITHUB_DATA_COLS["valuation"]])
            today_t      = _to_float(row[GITHUB_DATA_COLS["timing"]])
        except (IndexError, KeyError):
            continue

        prev = snapshot.get(sym)
        if prev is None or today_total is None or prev["total"] is None:
            continue

        score_delta = today_total - prev["total"]
        action_changed = today_action != prev["action"]
        if not (abs(score_delta) >= MATERIAL_SCORE_DELTA or action_changed):
            unchanged_count += 1
            continue

        dq = (today_q - prev["quality"]) if (today_q is not None and prev["quality"] is not None) else None
        dv = (today_v - prev["valuation"]) if (today_v is not None and prev["valuation"] is not None) else None
        dt = (today_t - prev["timing"]) if (today_t is not None and prev["timing"] is not None) else None
        rsi_delta = (today_rsi - prev["rsi"]) if (today_rsi is not None and prev["rsi"] is not None) else None
        pe_delta  = (today_pe - prev["pe"]) if (today_pe is not None and prev["pe"] is not None) else None

        dominant, dom_delta = _dominant_component(dq, dv, dt)

        changes.append({
            "symbol": sym,
            "score_delta": round(score_delta, 1),
            "prev_action": prev["action"],
            "today_action": today_action,
            "reason": _reason_text(dominant, dom_delta, rsi_delta, pe_delta),
            "why": _why_should_i_care(prev["action"], today_action, dominant, score_delta),
            "priority": _review_priority(prev["action"], today_action, score_delta),
        })

    top_improvements = sorted([c for c in changes if c["score_delta"] > 0],
                               key=lambda c: c["score_delta"], reverse=True)[:5]
    top_deteriorations = sorted([c for c in changes if c["score_delta"] < 0],
                                 key=lambda c: c["score_delta"])[:5]

    entered_strong_buy = sum(1 for c in changes
                              if c["today_action"] == "STRONG BUY" and c["prev_action"] != "STRONG BUY")
    digest = {
        "improved": len([c for c in changes if c["score_delta"] > 0]),
        "weakened": len([c for c in changes if c["score_delta"] < 0]),
        "entered_strong_buy": entered_strong_buy,
        "best_symbol": top_improvements[0]["symbol"] if top_improvements else None,
        "worst_symbol": top_deteriorations[0]["symbol"] if top_deteriorations else None,
    }

    log.info(f"Today's Changes vs {prev_date}: {len(top_improvements)} improved, "
             f"{len(top_deteriorations)} deteriorated, {unchanged_count} unchanged")

    return {
        "prev_date": prev_date,
        "top_improvements": top_improvements,
        "top_deteriorations": top_deteriorations,
        "unchanged_count": unchanged_count,
        "digest": digest,
    }


def format_telegram_digest(changes):
    """Short Telegram summary only — never the full stock list."""
    if not changes or changes.get("prev_date") is None or changes.get("digest") is None:
        return ""
    d = changes["digest"]
    lines = ["📊 <b>Today's Portfolio Changes</b>",
              f"• {d['improved']} stocks improved",
              f"• {d['weakened']} stocks weakened"]
    if d["entered_strong_buy"]:
        lines.append(f"• {d['entered_strong_buy']} entered STRONG BUY")
    if d["best_symbol"]:
        lines.append(f"• Best opportunity: {d['best_symbol']}")
    if d["worst_symbol"]:
        lines.append(f"• Biggest concern: {d['worst_symbol']}")
    return "\n".join(lines)
