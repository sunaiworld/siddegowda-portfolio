"""
Historical snapshot tracker + Today's Changes comparison.

GITHUB DATA and Future Buy tabs are ws.clear()'d and rewritten every
run — yesterday's score/PE/RSI is gone by the next run. This module
appends (never clears) one row per symbol per run to a "History" tab,
and one summary row per run to a "Portfolio History" tab.

It also compares today's in-memory results against the most recent
PRIOR TRADING DAY's History snapshot (not the previous run — multiple
cron/refresh runs on the same day collapse to that day's last row per
symbol) to produce deterministic, rule-based "Today's Changes" for
the Dashboard and a short Telegram digest. No new API calls — one
Sheets read of History, plus data already sitting in `results` from
run_portfolio_update().
"""
import logging
from datetime import datetime

log = logging.getLogger(__name__)

HISTORY_TAB = "History"
PORTFOLIO_HISTORY_TAB = "Portfolio History"

# Quality/Valuation/Timing are stored because Total Score alone can't
# be decomposed back into them (different mixes sum to the same
# total). Everything else needed for Today's Changes (RSI, PE) was
# already being stored for other reasons — no columns added for those.
HISTORY_HEADERS = ["Date", "Symbol", "CMP", "PE", "RSI", "Total Score",
                    "Final Action", "Quality", "Valuation", "Timing"]
_H = {name: i for i, name in enumerate(HISTORY_HEADERS)}

BUY_TIER  = {"STRONG BUY", "BUY"}
SELL_TIER = {"AVOID", "SELL"}

MATERIAL_SCORE_DELTA = 5  # |delta| >= this, OR action changed, = material

def _ensure_header_width(ws, headers):
    """If an existing tab's header row is narrower than `headers`
    (e.g. Portfolio History predates the Health Score column), widen
    just the header row. Never touches data rows."""
    current = ws.row_values(1)
    if len(current) < len(headers):
        ws.update('A1', [headers])


def _get_or_create(sh, tab_name, headers):
    try:
        ws = sh.worksheet(tab_name)
    except Exception:
        ws = sh.add_worksheet(tab_name, rows=5000, cols=len(headers))
        ws.append_row(headers)
    return ws


def _to_float(v):
    """Safe float parse — handles Sheets strings, in-memory numbers,
    and empty/missing values uniformly."""
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None

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
    last_row = same_day_rows[-1]  # last write wins if multiple runs that day
    health = _to_float(last_row[2]) if len(last_row) > 2 else None
    return prev_date, health


def append_history_snapshot(sh, results, portfolio_live_value, health_score=None):
    """
    results: the same row-lists build_result_row()/write_github_data()
    already use, indexed via main.GITHUB_DATA_COLS so this stays
    correct if the row layout shifts again. Call once per run, AFTER
    compute_todays_changes() has already read History for comparison
    — so "previous" never includes this run's own row.
    """
    from main import GITHUB_DATA_COLS  # lazy import — avoids circular import with main.py

    today = datetime.now().strftime("%Y-%m-%d")
    ws = _get_or_create(sh, HISTORY_TAB, HISTORY_HEADERS)

    snap_rows = []
    for row in results:
        try:
            sym       = row[GITHUB_DATA_COLS["symbol"]]
            cmp_      = row[GITHUB_DATA_COLS["cmp"]]
            pe        = row[GITHUB_DATA_COLS["pe"]]
            rsi       = row[GITHUB_DATA_COLS["rsi"]]
            total     = row[GITHUB_DATA_COLS["total"]]
            action    = row[GITHUB_DATA_COLS["action"]]
            quality   = row[GITHUB_DATA_COLS["quality"]]
            valuation = row[GITHUB_DATA_COLS["valuation"]]
            timing    = row[GITHUB_DATA_COLS["timing"]]
        except (IndexError, KeyError):
            continue
        snap_rows.append([today, sym, cmp_, pe, rsi, total, action, quality, valuation, timing])

    if snap_rows:
        ws.append_rows(snap_rows)
    log.info(f"History: {len(snap_rows)} symbol snapshots appended for {today}")

    ph_headers = ["Date", "Portfolio Value", "Health Score"]
    pws = _get_or_create(sh, PORTFOLIO_HISTORY_TAB, ph_headers)
    _ensure_header_width(pws, ph_headers)
    pws.append_row([today, round(portfolio_live_value, 2),
                     health_score if health_score is not None else ""])
    log.info(f"Portfolio History: value + health snapshot appended for {today}")


def _load_previous_trading_day_snapshot(sh):
    """
    Reads History once. Returns (prev_date_str, {symbol: row_dict})
    for the most recent date strictly before today. Multiple rows for
    the same symbol on that date collapse to the last one (append-only
    chronological order, so last-write-wins per symbol per date is
    correct). Returns (None, {}) if no prior trading day exists yet.
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


def _dominant_component(dq, dv, dt):
    """Whichever of Quality/Valuation/Timing moved the most, or
    (None, 0) if nothing moved by at least 2 points."""
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
    append_history_snapshot() in the same run, so the comparison
    never includes this run's own data.

    Returns:
        {
            "prev_date": "2026-07-11" or None,
            "top_improvements": [ {...}, ... up to 5 ],
            "top_deteriorations": [ {...}, ... up to 5 ],
            "unchanged_count": int,
            "digest": {improved, weakened, entered_strong_buy,
                       best_symbol, worst_symbol} or None,
        }
    """
    from main import GITHUB_DATA_COLS  # lazy import — avoids circular import with main.py

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
            continue  # no baseline — skip, per spec

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
