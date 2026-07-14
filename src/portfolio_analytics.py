"""
Portfolio-level dashboard.

Everything main.py currently reports is stock-centric (one row per
symbol). This module aggregates data run_portfolio_update() already
computed — holdings, fund_map, trades, portfolio_live_value — into
sector allocation, position concentration, portfolio beta, portfolio
XIRR, and expected dividend income. No new yfinance/API calls.
"""
import logging
from datetime import datetime, date

log = logging.getLogger(__name__)

DASHBOARD_TAB = "Dashboard"
CONCENTRATION_THRESHOLD_PCT = 5  # matches the 5%-weight-rule concept from the Apps Script build


def compute_portfolio_dashboard(holdings, fund_map, trades, portfolio_live_value):
    """
    holdings: {symbol: (qty, cmp, avg_buy)} — from run_portfolio_update()
    fund_map: {symbol: fundamentals_dict} — from run_portfolio_update()
    trades: raw Trade Log rows — from read_trades(sh)
    portfolio_live_value: float — from run_portfolio_update()
    """
    from main import compute_xirr  # lazy import — avoids circular import with main.py

    # ── Sector allocation ────────────────────
    sector_value = {}
    for sym, (qty, cmp, avg_buy) in holdings.items():
        sector = fund_map.get(sym, {}).get("sector") or "Unknown"
        sector_value[sector] = sector_value.get(sector, 0) + qty * cmp

    sector_alloc = []
    for sector, val in sorted(sector_value.items(), key=lambda x: x[1], reverse=True):
        pct = round(val / portfolio_live_value * 100, 2) if portfolio_live_value else 0
        sector_alloc.append([sector, round(val, 2), pct])

    # ── Position concentration ───────────────
    positions = []
    for sym, (qty, cmp, avg_buy) in sorted(holdings.items(), key=lambda x: x[1][0] * x[1][1], reverse=True):
        value = qty * cmp
        pct = round(value / portfolio_live_value * 100, 2) if portfolio_live_value else 0
        flag = "⚠️ Overweight" if pct > CONCENTRATION_THRESHOLD_PCT else ""
        positions.append([sym, round(value, 2), pct, flag])

    # ── Portfolio beta (value-weighted) ──────
    beta_sum, beta_weight = 0.0, 0.0
    for sym, (qty, cmp, avg_buy) in holdings.items():
        beta = fund_map.get(sym, {}).get("beta")
        value = qty * cmp
        if beta is not None:
            beta_sum += beta * value
            beta_weight += value
    portfolio_beta = round(beta_sum / beta_weight, 2) if beta_weight else None

    # ── Expected annual dividend income ──────
    div_income = 0.0
    for sym, (qty, cmp, avg_buy) in holdings.items():
        div_yield = fund_map.get(sym, {}).get("div")
        if div_yield:
            div_income += qty * cmp * (div_yield / 100)
    div_income = round(div_income, 2)

    # ── Portfolio-level XIRR (all trades combined) ──
    cashflows, dates = [], []
    for t in trades:
        if not t[0]:
            continue
        try:
            raw, dt = str(t[1]).strip(), None
            for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"]:
                try:
                    dt = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    pass
            if not dt:
                continue
            typ, qty, price = t[2].strip().upper(), float(t[3]), float(t[4])
            if typ == "BUY":
                cashflows.append(-qty * price); dates.append(dt)
            elif typ == "SELL":
                cashflows.append(qty * price); dates.append(dt)
        except (ValueError, IndexError):
            continue

    if portfolio_live_value:
        cashflows.append(portfolio_live_value)
        dates.append(date.today())

    portfolio_xirr = None
    if len(cashflows) >= 2:
        paired = sorted(zip(dates, cashflows))
        dates_sorted = [d for d, c in paired]
        cash_sorted = [c for d, c in paired]
        r = compute_xirr(cash_sorted, dates_sorted)
        portfolio_xirr = round(r * 100, 2) if r else None

    return {
        "sector_alloc": sector_alloc,
        "positions": positions,
        "portfolio_beta": portfolio_beta,
        "div_income": div_income,
        "portfolio_xirr": portfolio_xirr,
        "portfolio_value": round(portfolio_live_value, 2),
    }


def write_dashboard_tab(sh, dash, changes=None):
    """
    changes: optional dict from history_tracker.compute_todays_changes().
    Rendered right after the headline metrics — the "what changed"
    screen — ahead of Sector Allocation.
    """
    try:
        ws = sh.worksheet(DASHBOARD_TAB)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(DASHBOARD_TAB, rows=300, cols=4)

    rows = [
        ["SiddeGowda Portfolio — Dashboard", "", "", ""],
        ["Updated", datetime.now().strftime("%d-%b-%Y %H:%M"), "", ""],
        ["", "", "", ""],
        ["Portfolio Value", f"₹{dash['portfolio_value']:,.0f}", "", ""],
        ["Portfolio XIRR%", dash["portfolio_xirr"] if dash["portfolio_xirr"] is not None else "N/A", "", ""],
        ["Portfolio Beta", dash["portfolio_beta"] if dash["portfolio_beta"] is not None else "N/A", "", ""],
        ["Expected Div Income (annual)", f"₹{dash['div_income']:,.0f}", "", ""],
        ["", "", "", ""],
    ]

    if changes and changes.get("prev_date"):
        rows.append([f"Today's Changes (vs {changes['prev_date']})", "", "", ""])
        rows.append(["", "", "", ""])
        rows.append(["Top Improvements", "", "", "Priority"])
        for i, c in enumerate(changes["top_improvements"], 1):
            rows.append([f"{i}. {c['symbol']}", f"+{c['score_delta']}",
                         f"{c['prev_action']} → {c['today_action']}", c["priority"]])
            rows.append(["", f"Reason: {c['reason']}", "", ""])
            rows.append(["", f"Why: {c['why']}", "", ""])
        if not changes["top_improvements"]:
            rows.append(["(none)", "", "", ""])

        rows.append(["", "", "", ""])
        rows.append(["Top Deteriorations", "", "", "Priority"])
        for i, c in enumerate(changes["top_deteriorations"], 1):
            rows.append([f"{i}. {c['symbol']}", f"{c['score_delta']}",
                         f"{c['prev_action']} → {c['today_action']}", c["priority"]])
            rows.append(["", f"Reason: {c['reason']}", "", ""])
            rows.append(["", f"Why: {c['why']}", "", ""])
        if not changes["top_deteriorations"]:
            rows.append(["(none)", "", "", ""])

        rows.append(["", "", "", ""])
        rows.append([f"Unchanged Holdings: {changes['unchanged_count']}", "", "", ""])
        rows.append(["", "", "", ""])
    elif changes is not None:
        rows.append(["Today's Changes", "No prior trading day in History yet — check back tomorrow", "", ""])
        rows.append(["", "", "", ""])

    rows.append(["Sector Allocation", "", "", ""])
    rows.append(["Sector", "Value (₹)", "% of Portfolio", ""])
    for sector, val, pct in dash["sector_alloc"]:
        rows.append([sector, val, f"{pct}%", ""])

    rows.append(["", "", "", ""])
    rows.append(["Position Concentration", "", "", ""])
    rows.append(["Symbol", "Value (₹)", "% of Portfolio", "Flag"])
    for sym, val, pct, flag in dash["positions"]:
        rows.append([sym, val, f"{pct}%", flag])

    ws.append_rows(rows)
    log.info("Dashboard tab written")
    return ws
