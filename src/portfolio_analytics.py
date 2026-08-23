"""
Portfolio-level dashboard + Portfolio Health Score.

Everything main.py currently reports is stock-centric (one row per
symbol). This module aggregates data run_portfolio_update() already
computed — holdings, fund_map, trades, portfolio_live_value, results,
and (for the Dashboard KPIs/gainers/losers/signals) portfolio_builder's
combined_rows — into sector allocation, position concentration,
portfolio beta, portfolio XIRR, expected dividend income, and a
deterministic Portfolio Health Score. No new yfinance/API calls
anywhere in this file, and no portfolio math is recomputed — KPIs,
P&L, Return %, Weight %, and Signal all come straight from
portfolio_builder.build_portfolio()'s combined_rows.
"""
import logging
from datetime import datetime, date

log = logging.getLogger(__name__)

DASHBOARD_TAB = "Dashboard"
CONCENTRATION_THRESHOLD_PCT = 5  # matches the 5%-weight-rule concept from the Apps Script build

# Priority order for the "Action Required" list — most urgent first.
ACTION_PRIORITY = {
    "SELL - SL HIT": 0,
    "REQUIRES REVIEW (Corp Action)": 1,
    "BUY MORE": 2,
    "TARGET HIT - TRIM": 3,
}

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


def compute_portfolio_dashboard(holdings, fund_map, trades, portfolio_live_value, combined_rows=None):
    """
    holdings: {symbol: (qty, cmp, avg_buy)} — from run_portfolio_update()
    fund_map: {symbol: fundamentals_dict} — from run_portfolio_update()
    trades: raw Trade Log rows — from read_trades(sh)
    portfolio_live_value: float — from run_portfolio_update()
    combined_rows: list of per-symbol dicts from
        portfolio_builder.build_portfolio(prices)["combined"] — already
        has invested/value/pnl/return_pct/wt_pct/signal per symbol.
        Passed in (not recomputed) so the Dashboard KPIs always match
        the Portfolio tab exactly. Optional for backward compatibility;
        KPI/gainers/losers/signal keys are omitted if not supplied.
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

    # ── Position concentration (sorted desc by value; reused for the
    #    Top-5-holdings-+-Others allocation view and the concentration
    #    summary — the Dashboard no longer lists every holding) ──────
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

    dash = {
        "sector_alloc": sector_alloc,
        "positions": positions,
        "portfolio_beta": portfolio_beta,
        "div_income": div_income,
        "portfolio_xirr": portfolio_xirr,
        "portfolio_value": round(portfolio_live_value, 2),
    }

    # ── KPIs, Top Gainers/Losers, Signal counts, Action Required ────
    # Sourced entirely from portfolio_builder's combined_rows (already
    # computed invested/value/pnl/return_pct/wt_pct/signal per symbol)
    # so these numbers always reconcile with the Portfolio tab — no
    # second calculation of the same metric.
    combined_rows = combined_rows or []
    if combined_rows:
        invested_value = round(sum(r.get("invested", 0) for r in combined_rows), 2)
        total_pnl = round(dash["portfolio_value"] - invested_value, 2)
        return_pct = round((total_pnl / invested_value) * 100, 2) if invested_value else None

        ranked = sorted(combined_rows, key=lambda r: r.get("return_pct", 0) or 0, reverse=True)
        top_gainers = [r for r in ranked if (r.get("pnl") or 0) > 0][:5]
        top_losers = [r for r in ranked if (r.get("pnl") or 0) < 0][-5:][::-1]

        signal_counts = {}
        for r in combined_rows:
            sig = r.get("signal", "HOLD")
            signal_counts[sig] = signal_counts.get(sig, 0) + 1

        action_required = sorted(
            [r for r in combined_rows if r.get("signal") not in (None, "HOLD")],
            key=lambda r: ACTION_PRIORITY.get(r.get("signal"), 9)
        )

        dash.update({
            "invested_value": invested_value,
            "total_pnl": total_pnl,
            "return_pct": return_pct,
            "top_gainers": top_gainers,
            "top_losers": top_losers,
            "signal_counts": signal_counts,
            "action_required": action_required,
            "num_holdings": len(combined_rows),
            "largest_holding": positions[0] if positions else None,
            "top5_weight": round(sum(p[2] for p in positions[:5]), 2) if positions else 0,
        })

    log.info(f"[DEBUG] compute_portfolio_dashboard: portfolio_value={round(portfolio_live_value, 2)} "
              f"positions_generated={len(positions)} sectors_generated={len(sector_alloc)}")

    return dash


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
    Redesigned Dashboard: KPI band -> Health Score -> Top-5 Allocation
    -> Sector Allocation -> Top Gainers/Losers -> Signals & Action
    Required -> Concentration Summary -> Today's Changes. No per-symbol
    values are recomputed here — everything comes from `dash` (built by
    compute_portfolio_dashboard, which itself reuses portfolio_builder's
    combined_rows for anything money-related).
    """
    import sheet_formatter
    import sheet_writer
    try:
        ws = sh.worksheet(DASHBOARD_TAB)
        ws.clear()
        rules = sh.fetch_sheet_metadata({"includeGridData": False})
        sheet_meta = next((s for s in rules.get('sheets', []) if s.get('properties', {}).get('sheetId') == ws.id), None)
        if sheet_meta:
            cond_formats = sheet_meta.get("conditionalFormats", [])
            if cond_formats:
                clear_reqs = [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}} for _ in cond_formats]
                sheet_writer.batch_update_safe(sh, clear_reqs)
    except Exception:
        ws = sh.add_worksheet(DASHBOARD_TAB, rows=400, cols=4)

    rows = []
    header_indices = []
    subheader_indices = []
    pos_neg_cells = []   # (r_idx, c_idx, val)
    currency_cells = []  # (r_idx, c_idx)
    pct_cells = []       # (r_idx, c_idx)
    signal_cells = []    # (r_idx, c_idx, signal_str)
    kpi_band_row = None  # row index of the 4-up KPI value row, for larger font

    def add_row(r_data, is_header=False, is_subheader=False):
        rows.append(r_data)
        idx = len(rows)
        if is_header:
            header_indices.append(idx)
        if is_subheader:
            subheader_indices.append(idx)
        return idx

    # ── 1. Header ─────────────────────────────
    add_row(["PORTFOLIO DASHBOARD", "", "", ""], is_header=True)
    add_row([f"Last Updated: {datetime.now().strftime('%d-%b-%Y %H:%M')}", "", "", ""])
    add_row(["", "", "", ""])

    # ── 2. KPI band ───────────────────────────
    has_kpis = "invested_value" in dash
    if has_kpis:
        add_row(["PORTFOLIO VALUE", "INVESTED VALUE", "TOTAL P&L", "RETURN %"], is_subheader=True)
        kpi_band_row = add_row([
            dash["portfolio_value"], dash["invested_value"], dash["total_pnl"],
            (dash["return_pct"] / 100.0) if dash.get("return_pct") is not None else "N/A",
        ])
        currency_cells.append((kpi_band_row, 0))
        currency_cells.append((kpi_band_row, 1))
        currency_cells.append((kpi_band_row, 2))
        pos_neg_cells.append((kpi_band_row, 2, dash["total_pnl"]))
        if dash.get("return_pct") is not None:
            pct_cells.append((kpi_band_row, 3))
            pos_neg_cells.append((kpi_band_row, 3, dash["return_pct"]))
        add_row(["", "", "", ""])
    else:
        add_row(["Portfolio Value", dash["portfolio_value"], "", ""], is_subheader=True)
        add_row(["", "", "", ""])

    # ── 3. Portfolio Health Score ─────────────
    if health:
        trend_label, delta = health_trend if health_trend else ("-", None)
        delta_str = ""
        if delta is not None:
            sign = "+" if delta > 0 else ""
            delta_str = f" ({sign}{delta} vs prior trading day)"

        add_row(["Portfolio Health Score", f"{health['overall']} / 100", health["grade"], f"{trend_label}{delta_str}"], is_header=True)
        add_row(["", "", "", ""])
        add_row(["Component", "Score", "Bar", ""], is_subheader=True)
        for name, score in health["components"].items():
            weight_pct = int(health["weights"].get(HEALTH_COMPONENT_KEYS.get(name, ""), 0) * 100)
            filled = int(round(score / 10))
            bar = "█" * filled + "░" * (10 - filled)
            add_row([f"{name} ({weight_pct}%)", score, bar, ""])
        add_row(["", "", "", ""])

    # ── 4. Portfolio allocation: Top 5 holdings + Others ──
    if dash.get("positions"):
        add_row(["Top Holdings", "", "", ""], is_header=True)
        add_row(["Symbol", "Value", "% of Portfolio", ""], is_subheader=True)
        top5 = dash["positions"][:5]
        rest = dash["positions"][5:]
        for sym, val, pct, flag in top5:
            r_idx = add_row([sym, val, pct / 100.0 if pct else 0.0, flag])
            currency_cells.append((r_idx, 1))
            pct_cells.append((r_idx, 2))
        if rest:
            other_val = round(sum(p[1] for p in rest), 2)
            other_pct = round(sum(p[2] for p in rest), 2)
            r_idx = add_row([f"Others ({len(rest)})", other_val, other_pct / 100.0 if other_pct else 0.0, ""])
            currency_cells.append((r_idx, 1))
            pct_cells.append((r_idx, 2))
        add_row(["", "", "", ""])

    # ── 5. Sector allocation ──────────────────
    if dash.get("sector_alloc"):
        add_row(["Sector Allocation", "", "", ""], is_header=True)
        add_row(["Sector", "Value", "% of Portfolio", ""], is_subheader=True)
        for sector, val, pct in dash["sector_alloc"]:
            r_idx = add_row([sector, val, pct / 100.0 if pct else 0.0, ""])
            currency_cells.append((r_idx, 1))
            pct_cells.append((r_idx, 2))
        add_row(["", "", "", ""])

    # ── 6. Top Gainers / Top Losers ───────────
    if has_kpis:
        add_row(["Top Gainers", "P&L", "Return %", ""], is_subheader=True)
        if dash["top_gainers"]:
            for r in dash["top_gainers"]:
                r_idx = add_row([r["symbol"], r["pnl"], r["return_pct"] / 100.0, ""])
                currency_cells.append((r_idx, 1))
                pct_cells.append((r_idx, 2))
                pos_neg_cells.append((r_idx, 1, r["pnl"]))
                pos_neg_cells.append((r_idx, 2, r["return_pct"]))
        else:
            add_row(["(none)", "", "", ""])
        add_row(["", "", "", ""])

        add_row(["Top Losers", "P&L", "Return %", ""], is_subheader=True)
        if dash["top_losers"]:
            for r in dash["top_losers"]:
                r_idx = add_row([r["symbol"], r["pnl"], r["return_pct"] / 100.0, ""])
                currency_cells.append((r_idx, 1))
                pct_cells.append((r_idx, 2))
                pos_neg_cells.append((r_idx, 1, r["pnl"]))
                pos_neg_cells.append((r_idx, 2, r["return_pct"]))
        else:
            add_row(["(none)", "", "", ""])
        add_row(["", "", "", ""])

        # ── 7. Portfolio Signals + Action Required ──
        add_row(["Portfolio Signals", "", "", ""], is_header=True)
        sc = dash["signal_counts"]
        signal_summary_row = [
            f"HOLD: {sc.get('HOLD', 0)}",
            f"BUY MORE@: {sc.get('BUY MORE', 0)}",
            f"SELL - SL HIT: {sc.get('SELL - SL HIT', 0)}",
            f"TARGET HIT: {sc.get('TARGET HIT - TRIM', 0)}",
        ]
        add_row(signal_summary_row, is_subheader=True)
        add_row(["", "", "", ""])

        add_row(["Action Required", "Signal", "P&L", "Return %"], is_subheader=True)
        if dash["action_required"]:
            for r in dash["action_required"]:
                r_idx = add_row([r["symbol"], r["signal"], r["pnl"], r["return_pct"] / 100.0])
                currency_cells.append((r_idx, 2))
                pct_cells.append((r_idx, 3))
                signal_cells.append((r_idx, 1, r["signal"]))
        else:
            add_row(["(none — all holdings within Hold range)", "", "", ""])
        add_row(["", "", "", ""])

        # ── 8. Concentration / Summary ────────────
        add_row(["Portfolio Summary", "", "", ""], is_header=True)
        idx = add_row(["Number of Holdings", dash["num_holdings"], "", ""])
        if dash.get("largest_holding"):
            lh = dash["largest_holding"]
            add_row(["Largest Holding", f"{lh[0]} ({lh[2]}%)", "", ""])
        add_row(["Top 5 Holdings Combined Weight", f"{dash['top5_weight']}%", "", ""])
        if dash.get("sector_alloc"):
            top_sector = dash["sector_alloc"][0]
            add_row(["Largest Sector", f"{top_sector[0]} ({top_sector[2]}%)", "", ""])
        val = dash["portfolio_xirr"]
        if val is not None:
            idx = add_row(["Portfolio XIRR", val / 100.0, "", ""])
            pct_cells.append((idx, 1))
            pos_neg_cells.append((idx, 1, val))
        val = dash["portfolio_beta"]
        add_row(["Portfolio Beta", val if val is not None else "N/A", "", ""])
        idx = add_row(["Expected Div Income (annual)", dash['div_income'], "", ""])
        currency_cells.append((idx, 1))
        add_row(["", "", "", ""])
    else:
        # combined_rows wasn't supplied — fall back to the beta/XIRR/div-only
        # KPI set rather than fabricating P&L/Return%/gainers/losers.
        add_row(["Portfolio KPIs", "", "", ""], is_header=True)
        val = dash["portfolio_xirr"]
        if val is not None:
            idx = add_row(["Portfolio XIRR", val / 100.0, "", ""])
            pct_cells.append((idx, 1))
            pos_neg_cells.append((idx, 1, val))
        else:
            add_row(["Portfolio XIRR%", "N/A", "", ""])
        val = dash["portfolio_beta"]
        add_row(["Portfolio Beta", val if val is not None else "N/A", "", ""])
        idx = add_row(["Expected Div Income (annual)", dash['div_income'], "", ""])
        currency_cells.append((idx, 1))
        add_row(["", "", "", ""])

    # ── 9. Today's Changes (unchanged behaviour) ──
    if changes and changes.get("prev_date"):
        add_row([f"Today's Changes (vs {changes['prev_date']})", "", "", ""], is_header=True)
        add_row(["", "", "", ""])

        add_row(["Top Improvements", "Score Delta", "Action Change", "Priority"], is_subheader=True)
        for i, c in enumerate(changes["top_improvements"], 1):
            r_idx = add_row([f"{i}. {c['symbol']}", c['score_delta'], f"{c['prev_action']} → {c['today_action']}", c["priority"]])
            pos_neg_cells.append((r_idx, 1, c['score_delta']))
            add_row(["", f"Reason: {c['reason']}", "", ""])
            add_row(["", f"Why: {c['why']}", "", ""])
        if not changes["top_improvements"]:
            add_row(["(none)", "", "", ""])

        add_row(["", "", "", ""])
        add_row(["Top Deteriorations", "Score Delta", "Action Change", "Priority"], is_subheader=True)
        for i, c in enumerate(changes["top_deteriorations"], 1):
            r_idx = add_row([f"{i}. {c['symbol']}", c['score_delta'], f"{c['prev_action']} → {c['today_action']}", c["priority"]])
            pos_neg_cells.append((r_idx, 1, c['score_delta']))
            add_row(["", f"Reason: {c['reason']}", "", ""])
            add_row(["", f"Why: {c['why']}", "", ""])
        if not changes["top_deteriorations"]:
            add_row(["(none)", "", "", ""])

        add_row(["", "", "", ""])
        add_row([f"Unchanged Holdings: {changes['unchanged_count']}", "", "", ""])
    elif changes is not None:
        add_row(["Today's Changes", "No prior trading day in History yet - check back tomorrow", "", ""], is_header=True)

    ws.update("A1", rows, value_input_option="RAW")

    nc = 4
    widths = [260, 150, 180, 220]
    reqs = sheet_formatter.clear_all_formatting_reqs(ws.id) + sheet_formatter.get_structural_format_reqs(
        ws.id, len(rows), nc, widths=widths, freeze_rows=0, freeze_cols=0)

    for h_idx in header_indices:
        for col in range(nc):
            reqs.append(sheet_formatter.color_cell_req(ws.id, h_idx, col, "0d1b2a", "ffffff", font_size=8))

    for s_idx in subheader_indices:
        for col in range(nc):
            reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx, col, "1c3144", "ffffff", font_size=8))

    if kpi_band_row is not None:
        for col in range(4):
            reqs.append({
                "repeatCell": {
                    "range": {"sheetId": ws.id, "startRowIndex": kpi_band_row - 1, "endRowIndex": kpi_band_row,
                              "startColumnIndex": col, "endColumnIndex": col + 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"fontSize": 14, "bold": True}}},
                    "fields": "userEnteredFormat.textFormat"
                }
            })
        reqs.append({
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "ROWS", "startIndex": kpi_band_row - 1, "endIndex": kpi_band_row},
                "properties": {"pixelSize": 36}, "fields": "pixelSize"
            }
        })

    for r_idx, c_idx, val in pos_neg_cells:
        req = sheet_formatter.color_positive_negative(ws.id, r_idx, c_idx, val)
        if req: reqs.append(req)

    for r_idx, c_idx in currency_cells:
        reqs += sheet_formatter.get_currency_format_reqs(ws.id, r_idx - 1, r_idx, c_idx, c_idx + 1)

    for r_idx, c_idx in pct_cells:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, r_idx - 1, r_idx, c_idx, c_idx + 1)

    # Action Required signal cells get the same subtle colour coding
    # used on the Portfolio tab (soft red/blue/green), for consistency.
    for r_idx, c_idx, sig in signal_cells:
        req = sheet_formatter.color_action_signal(ws.id, r_idx, c_idx, sig)
        if req: reqs.append(req)

    if reqs:
        sheet_writer.batch_update_safe(sh, reqs)

    log.info("Dashboard tab written with enhanced formatting")
    return ws
