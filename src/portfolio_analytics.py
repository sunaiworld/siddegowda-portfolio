"""
Portfolio-level dashboard + Portfolio Health Score.

Everything main.py currently reports is stock-centric (one row per
symbol). This module aggregates data run_portfolio_update() already
computed — holdings, fund_map, trades, portfolio_live_value, results —
into sector allocation, position concentration, portfolio beta,
portfolio XIRR, expected dividend income, and a deterministic
Portfolio Health Score. No new yfinance/API calls anywhere in this file.
"""
import logging
from datetime import datetime, date

log = logging.getLogger(__name__)

DASHBOARD_TAB = "Dashboard"
CONCENTRATION_THRESHOLD_PCT = 5  # matches the 5%-weight-rule concept from the Apps Script build

# ── Portfolio Health Score weights ───────────
# Growth deliberately excluded — Rev Growth% already feeds the
# stock-level Quality Score via SECTOR_RULES; a separate Growth
# component here would double-count the same input.
HEALTH_WEIGHTS = {
    "quality": 0.30,
    "valuation": 0.25,
    "risk": 0.20,
    "diversification": 0.15,
    "momentum": 0.10,
}
HEALTH_COMPONENT_KEYS = {
    "Business Quality": "quality",
    "Valuation": "valuation",
    "Risk": "risk",
    "Diversification": "diversification",
    "Momentum": "momentum",
}
HEALTH_GRADE_TABLE = [(95, "A+"), (90, "A"), (85, "A-"), (80, "B+"), (75, "B"), (70, "C")]
HEALTH_TREND_THRESHOLD = 2  # |delta| >= this = Improving/Weakening, else Stable


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _to_f(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _health_grade(score):
    for threshold, grade in HEALTH_GRADE_TABLE:
        if score >= threshold:
            return grade
    return "Needs Attention"


def compute_health_trend(overall, prev_score):
    """Returns (label, delta). delta is None if no prior trading day exists."""
    if prev_score is None:
        return "—", None
    delta = round(overall - prev_score, 1)
    if delta >= HEALTH_TREND_THRESHOLD:
        return "📈 Improving", delta
    if delta <= -HEALTH_TREND_THRESHOLD:
        return "📉 Weakening", delta
    return "➡️ Stable", delta


def compute_portfolio_dashboard(holdings, fund_map, trades, portfolio_live_value):
    """
    holdings: {symbol: (qty, cmp, avg_buy)} — from run_portfolio_update()
    fund_map: {symbol: fundamentals_dict} — from run_portfolio_update()
    trades: raw Trade Log rows — from read_trades(sh)
    portfolio_live_value: float — from run_portfolio_update()
    """
    from portfolio_builder import compute_xirr  # avoids circular import: portfolio_builder doesn't import portfolio_analytics

    log.info(f"[DEBUG] compute_portfolio_dashboard: holdings received = {len(holdings)}")

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

    log.info(f"[DEBUG] compute_portfolio_dashboard: portfolio_value={round(portfolio_live_value, 2)} "
              f"positions_generated={len(positions)} sectors_generated={len(sector_alloc)}")

    return {
        "sector_alloc": sector_alloc,
        "positions": positions,
        "portfolio_beta": portfolio_beta,
        "div_income": div_income,
        "portfolio_xirr": portfolio_xirr,
        "portfolio_value": round(portfolio_live_value, 2),
    }


def compute_portfolio_health(results, holdings, fund_map, dash):
    """
    Deterministic Portfolio Health Score (0-100), five value-weighted
    components. Reuses:
    - Quality/Valuation/Timing scores already in `results`
    - debt_eq already in `fund_map`
    - portfolio_beta, sector_alloc, positions already in `dash`
      (from compute_portfolio_dashboard() — not recomputed here)
    No new fetches, no new pass over raw data beyond what's given.
    """
    from github_data_builder import GITHUB_DATA_COLS  # avoids circular import: github_data_builder doesn't import portfolio_analytics

    row_by_sym = {}
    for row in results:
        try:
            row_by_sym[row[GITHUB_DATA_COLS["symbol"]]] = row
        except (IndexError, KeyError):
            continue

    q_sum = v_sum = t_sum = w_sum = 0.0
    debt_sum, debt_w = 0.0, 0.0
    for sym, (qty, cmp, avg_buy) in holdings.items():
        row = row_by_sym.get(sym)
        if row is None:
            continue
        value = qty * cmp

        q = _to_f(row[GITHUB_DATA_COLS["quality"]])
        v = _to_f(row[GITHUB_DATA_COLS["valuation"]])
        t = _to_f(row[GITHUB_DATA_COLS["timing"]])
        if q is not None:
            q_sum += q * value
        if v is not None:
            v_sum += v * value
        if t is not None:
            t_sum += t * value
        w_sum += value

        debt = fund_map.get(sym, {}).get("debt_eq")
        if debt is not None:
            debt_sum += debt * value
            debt_w += value

    log.info(f"[DEBUG] compute_portfolio_health: holdings_count={len(holdings)} w_sum={w_sum} "
              f"quality_sum={q_sum} valuation_sum={v_sum} timing_sum={t_sum} "
              f"debt_sum={debt_sum} debt_w={debt_w} portfolio_beta_from_dash={dash.get('portfolio_beta')}")

    quality_component   = _clamp((q_sum / w_sum) / 40 * 100, 0, 100) if w_sum else 0
    valuation_component = _clamp((v_sum / w_sum) / 30 * 100, 0, 100) if w_sum else 0
    momentum_component  = _clamp((t_sum / w_sum) / 30 * 100, 0, 100) if w_sum else 0
    avg_debt_eq = (debt_sum / debt_w) if debt_w else 0

    # ── Risk: beta + debt + concentration penalties ──
    portfolio_beta = dash.get("portfolio_beta") or 0.8
    beta_penalty = _clamp((portfolio_beta - 0.8) * 40, 0, 40)
    debt_penalty = _clamp(avg_debt_eq * 15, 0, 30)
    overweight_count = sum(1 for pos in dash.get("positions", []) if len(pos) > 3 and pos[3])
    concentration_penalty = 5 * overweight_count
    risk_component = _clamp(100 - beta_penalty - debt_penalty - concentration_penalty, 0, 100)

    # ── Diversification: HHI across sectors ──
    hhi = sum((pos[2] or 0) ** 2 for pos in dash.get("sector_alloc", []) if len(pos) > 2)
    diversification_component = _clamp(100 - hhi / 100, 0, 100)

    components = {
        "Business Quality": round(quality_component, 1),
        "Valuation": round(valuation_component, 1),
        "Risk": round(risk_component, 1),
        "Diversification": round(diversification_component, 1),
        "Momentum": round(momentum_component, 1),
    }

    overall = sum(HEALTH_WEIGHTS[HEALTH_COMPONENT_KEYS[name]] * score for name, score in components.items())
    overall = round(overall, 1)

    return {
        "overall": overall,
        "grade": _health_grade(overall),
        "components": components,
        "weights": HEALTH_WEIGHTS,
    }


def write_dashboard_tab(sh, dash, changes=None, health=None, health_trend=None):
    """
    health: dict from compute_portfolio_health(), rendered as the
    first section of the Dashboard.
    health_trend: (label, delta) tuple from compute_health_trend().
    changes: dict from history_tracker.compute_todays_changes().
    """
    try:
        ws = sh.worksheet(DASHBOARD_TAB)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(DASHBOARD_TAB, rows=400, cols=4)

    rows = [
        ["SiddeGowda Portfolio — Dashboard", "", "", ""],
        ["Updated", datetime.now().strftime("%d-%b-%Y %H:%M"), "", ""],
        ["", "", "", ""],
    ]

    if health:
        trend_label, delta = health_trend if health_trend else ("—", None)
        delta_str = ""
        if delta is not None:
            sign = "+" if delta > 0 else ""
            delta_str = f" ({sign}{delta} vs prior trading day)"
        rows.append(["Portfolio Health Score", f"{health['overall']} / 100", health["grade"],
                     f"{trend_label}{delta_str}"])
        rows.append(["", "", "", ""])
        for name, score in health["components"].items():
            weight_pct = int(health["weights"][HEALTH_COMPONENT_KEYS[name]] * 100)
            filled = int(round(score / 10))
            bar = "█" * filled + "░" * (10 - filled)
            rows.append([f"{name} ({weight_pct}%)", score, bar, ""])
        rows.append(["", "", "", ""])

    rows += [
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
