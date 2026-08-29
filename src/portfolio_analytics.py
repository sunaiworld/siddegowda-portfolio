"""
Portfolio-level dashboard + Portfolio Health Score.

Everything main.py currently reports is stock-centric (one row per
symbol). This module aggregates data run_portfolio_update() already
computed — holdings, fund_map, trades, portfolio_live_value, results,
and (for the Dashboard KPIs/gainers/losers/signals/sector P&L)
portfolio_builder's combined_rows — into sector allocation, position
concentration, portfolio beta, portfolio XIRR, expected dividend
income, and a deterministic Portfolio Health Score. No new
yfinance/API calls anywhere in this file, and no portfolio math is
recomputed — KPIs, P&L, Return %, Weight %, and Signal all come
straight from portfolio_builder.build_portfolio()'s combined_rows.

Sector classification: fund_map[sym]["sector"] (from yfinance, cached
by fund_cache.py) is the only sector source anywhere in this codebase
— reused as-is here, never guessed from a symbol name. A symbol with
no sector data from yfinance falls into an "Unknown" bucket rather
than being assigned a sector.
"""
import logging
from datetime import datetime, date

log = logging.getLogger(__name__)

DASHBOARD_TAB = "Dashboard"
CONCENTRATION_THRESHOLD_PCT = 5  # matches the 5%-weight-rule concept from the Apps Script build
# No target/ideal sector-allocation source exists anywhere in this codebase, so
# the Dashboard doesn't display a "Recommended %" column (would be a fabricated
# number). Instead, a sector is flagged when its weight passes this threshold —
# a conventional diversified-equity-portfolio guideline (no single sector above
# ~25-30%), not a per-sector target pulled from any data source.
SECTOR_CONCENTRATION_THRESHOLD_PCT = 25

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
        KPI/gainers/losers/signal/sector-P&L keys are omitted if not
        supplied.
    """
    from portfolio_builder import compute_xirr  # avoids circular import: portfolio_builder doesn't import portfolio_analytics

    log.info(f"[DEBUG] compute_portfolio_dashboard: holdings received = {len(holdings)}")

    # ── Sector allocation (value/weight only — used by the Health
    #    Score's diversification calc, kept exactly as before) ──────
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
        r = compute_xirr(cash_sorted, dates_sorted)
        portfolio_xirr = round(r * 100, 2) if r else None

    beta_covered_pct = round((beta_weight / portfolio_live_value * 100), 1) if portfolio_live_value > 0 else 0

    dash = {
        "sector_alloc": sector_alloc,
        "positions": positions,
        "portfolio_beta": portfolio_beta,
        "beta_covered_pct": beta_covered_pct,
        "beta_covered_value": round(beta_weight, 2),
        "div_income": div_income,
        "portfolio_xirr": portfolio_xirr,
        "portfolio_value": round(portfolio_live_value, 2),
    }

    # ── KPIs, Top Gainers/Losers, Signal counts, Action Required,
    #    Sector P&L/Return% breakdown ─────────
    # Sourced entirely from portfolio_builder's combined_rows (already
    # computed invested/value/pnl/return_pct/wt_pct/signal per symbol)
    # so these numbers always reconcile with the Portfolio tab — no
    # second calculation of the same metric. Sector is joined in from
    # fund_map (the same source sector_alloc above already uses).
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

        action_required_raw = sorted(
            [r for r in combined_rows if r.get("signal") not in (None, "HOLD")],
            key=lambda r: ACTION_PRIORITY.get(r.get("signal"), 9)
        )
        action_required = []
        for r in action_required_raw:
            r_item = dict(r)
            src = (r.get("investment_source") or "").upper()
            if src == "SMALLCASE":
                r_item["symbol_display"] = f"{r['symbol']} (SC)"
            elif src == "ETF":
                r_item["symbol_display"] = f"{r['symbol']} (ETF)"
            else:
                r_item["symbol_display"] = r["symbol"]
            action_required.append(r_item)

        # 1W and 1M momentum counts across portfolio holdings
        up_1w = sum(1 for r in combined_rows if (r.get("return_1w") or 0) > 0)
        dn_1w = sum(1 for r in combined_rows if (r.get("return_1w") or 0) < 0)
        up_1m = sum(1 for r in combined_rows if (r.get("return_1m") or 0) > 0)
        dn_1m = sum(1 for r in combined_rows if (r.get("return_1m") or 0) < 0)

        # Sector-level rollup + Source counting + Portfolio Impact
        sector_agg = {}
        source_summary = {
            "SELF": {"count": 0, "invested": 0.0, "value": 0.0},
            "SMALLCASE": {"count": 0, "invested": 0.0, "value": 0.0},
            "ETF": {"count": 0, "invested": 0.0, "value": 0.0},
            "LEGACY": {"count": 0, "invested": 0.0, "value": 0.0},
            "UNKNOWN": {"count": 0, "invested": 0.0, "value": 0.0},
        }

        for r in combined_rows:
            sym = r.get("symbol")
            val = r.get("value", 0) or 0
            inv = r.get("invested", 0) or 0
            wt = r.get("wt_pct", 0) or 0
            ret = r.get("return_pct", 0) or 0
            
            # Portfolio Impact %
            r["portfolio_impact"] = round((wt * ret) / 100, 2)

            # Sector label: ETFs are identified by investment_source (the most
            # reliable signal — derived from source_map / broker trade data /
            # yfinance sector=="ETFs" in portfolio_builder). A stock with a
            # missing yfinance sector falls to "Unknown" — NOT "ETFs". This
            # separates genuine ETFs from stocks that happen to lack sector data.
            inv_src = r.get("investment_source", "").upper()
            yf_sector = fund_map.get(sym, {}).get("sector", "") or ""
            if inv_src == "ETF" or yf_sector == "ETFs":
                sector = "ETFs"
            else:
                sector = yf_sector or "Unknown"
            agg = sector_agg.setdefault(sector, {"count": 0, "invested": 0.0, "value": 0.0, "beta_sum": 0.0, "beta_weight": 0.0})
            agg["count"] += 1
            agg["invested"] += inv
            agg["value"] += val
            
            beta = fund_map.get(sym, {}).get("beta")
            if beta is not None:
                agg["beta_sum"] += beta * val
                agg["beta_weight"] += val

            src = r.get("investment_source", "UNKNOWN").upper()
            if src not in source_summary:
                src = "LEGACY" if src == "LEGACY" else "UNKNOWN"
            source_summary[src]["count"] += 1
            source_summary[src]["invested"] += inv
            source_summary[src]["value"] += val

        sector_detail = []
        for sector, agg in sorted(sector_agg.items(), key=lambda x: x[1]["value"], reverse=True):
            s_pnl = round(agg["value"] - agg["invested"], 2)
            s_return_pct = round((s_pnl / agg["invested"]) * 100, 2) if agg["invested"] else None
            s_weight = round(agg["value"] / dash["portfolio_value"] * 100, 2) if dash["portfolio_value"] else 0
            flag = "⚠️ Concentrated" if s_weight > SECTOR_CONCENTRATION_THRESHOLD_PCT else ""
            
            s_beta = round(agg["beta_sum"] / agg["beta_weight"], 2) if agg["beta_weight"] > 0 else None
            
            sector_detail.append({
                "sector": sector, "count": agg["count"], "weight_pct": s_weight,
                "invested": round(agg["invested"], 2), "value": round(agg["value"], 2),
                "pnl": s_pnl, "return_pct": s_return_pct, "beta": s_beta, "flag": flag,
            })

        top3_sector_weight = round(sum(s["weight_pct"] for s in sector_detail[:3]), 2) if sector_detail else 0
        
        impact_ranked = sorted(combined_rows, key=lambda r: r.get("portfolio_impact", 0) or 0, reverse=True)
        top_positive_impact = [r for r in impact_ranked if (r.get("portfolio_impact") or 0) > 0][:5]
        top_negative_impact = [r for r in impact_ranked if (r.get("portfolio_impact") or 0) < 0][-5:][::-1]
        
        sector_pnl_ranked = sorted(sector_detail, key=lambda s: s["pnl"], reverse=True)
        top_positive_sectors = [s for s in sector_pnl_ranked if s["pnl"] > 0][:3]
        top_negative_sectors = [s for s in sector_pnl_ranked if s["pnl"] < 0][-3:][::-1]

        dash.update({
            "invested_value": invested_value,
            "total_pnl": total_pnl,
            "return_pct": return_pct,
            "top_gainers": top_gainers,
            "top_losers": top_losers,
            "top_positive_impact": top_positive_impact,
            "top_negative_impact": top_negative_impact,
            "top_positive_sectors": top_positive_sectors,
            "top_negative_sectors": top_negative_sectors,
            "source_summary": source_summary,
            "signal_counts": signal_counts,
            "action_required": action_required,
            "num_holdings": len(combined_rows),
            "largest_holding": positions[0] if positions else None,
            "top5_weight": round(sum(p[2] for p in positions[:5]), 2) if positions else 0,
            "sector_detail": sector_detail,
            "num_sectors": len(sector_detail),
            "top3_sector_weight": top3_sector_weight,
            "momentum_1w": (up_1w, dn_1w),
            "momentum_1m": (up_1m, dn_1m),
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
    -> Sector Allocation (+ bar chart) -> Top Gainers/Losers -> Signals
    & Action Required -> Concentration Summary -> Today's Changes. No
    per-symbol values are recomputed here — everything comes from
    `dash` (built by compute_portfolio_dashboard, which itself reuses
    portfolio_builder's combined_rows for anything money-related).

    Row/column indexing convention used throughout this function:
    add_row() returns a 1-based row number matching the sheet's visible
    row (row 1 = the first row written). sheet_formatter helpers take
    0-indexed API row numbers, so every reference below subtracts 1
    (r_idx - 1) when calling them — get this wrong and colours land one
    row below the content they're meant to highlight.
    """
    import sheet_formatter
    import sheet_writer
    try:
        ws = sh.worksheet(DASHBOARD_TAB)
    except Exception:
        ws = sh.add_worksheet(DASHBOARD_TAB, rows=400, cols=8)

    sheet_writer.clear_sheet_safe(ws)
    rules = sh.fetch_sheet_metadata({"includeGridData": False})
    sheet_meta = next((s for s in rules.get('sheets', []) if s.get('properties', {}).get('sheetId') == ws.id), None)
    if sheet_meta:
        cond_formats = sheet_meta.get("conditionalFormats", [])
        if cond_formats:
            clear_reqs = [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}} for _ in cond_formats]
            sheet_writer.batch_update_safe(sh, clear_reqs)
        existing_charts = sheet_meta.get("charts", [])
        if existing_charts:
            sheet_writer.batch_update_safe(sh, [
                {"deleteEmbeddedObject": {"objectId": c["chartId"]}} for c in existing_charts
            ])

    nc = 9  # widened from 4 to fit the Sector Allocation table's 8 columns (incl. concentration flag); other sections just leave the extra columns blank
    rows = []
    header_indices = []
    subheader_indices = []
    pos_neg_cells = []   # (r_idx, c_idx, val)
    currency_cells = []  # (r_idx, c_idx)
    pct_cells = []       # (r_idx, c_idx)
    signal_cells = []    # (r_idx, c_idx, signal_str)
    kpi_band_row = None  # row index of the 4-up KPI value row, for larger font
    sector_table_rows = None  # (first_data_row_1idx, last_data_row_1idx) for the chart source range

    def pad(r_data):
        return r_data + [""] * (nc - len(r_data))

    def add_row(r_data, is_header=False, is_subheader=False):
        rows.append(pad(r_data))
        idx = len(rows)
        if is_header:
            header_indices.append(idx)
        if is_subheader:
            subheader_indices.append(idx)
        return idx

    # ── 1. Header ─────────────────────────────
    add_row(["PORTFOLIO DASHBOARD"], is_header=True)
    add_row([f"Last Updated: {datetime.now().strftime('%d-%b-%Y %H:%M')}"])
    add_row([])

    # ── 2. KPI band ───────────────────────────
    has_kpis = "invested_value" in dash
    if has_kpis:
        add_row(["PORTFOLIO VALUE", "INVESTED VALUE", "TOTAL P&L", "RETURN %"], is_subheader=True)
        kpi_band_row = add_row([
            dash["portfolio_value"], dash["invested_value"], dash["total_pnl"],
            dash["return_pct"] if dash.get("return_pct") is not None else "N/A",
        ])
        currency_cells.append((kpi_band_row, 0))
        currency_cells.append((kpi_band_row, 1))
        currency_cells.append((kpi_band_row, 2))
        pos_neg_cells.append((kpi_band_row, 2, dash["total_pnl"]))
        if dash.get("return_pct") is not None:
            pct_cells.append((kpi_band_row, 3))
            pos_neg_cells.append((kpi_band_row, 3, dash["return_pct"]))
        add_row([])
    else:
        add_row(["Portfolio Value", dash["portfolio_value"]], is_subheader=True)
        add_row([])

    # ── 3. Portfolio Health Score ─────────────
    if health:
        trend_label, delta = health_trend if health_trend else ("-", None)
        delta_str = ""
        if delta is not None:
            sign = "+" if delta > 0 else ""
            delta_str = f" ({sign}{delta} vs prior trading day)"

        add_row(["Portfolio Health Score", f"{health['overall']} / 100", health["grade"], f"{trend_label}{delta_str}"], is_header=True)
        add_row([])
        add_row(["Component", "Score", "Bar"], is_subheader=True)
        for name, score in health["components"].items():
            weight_pct = int(health["weights"].get(HEALTH_COMPONENT_KEYS.get(name, ""), 0) * 100)
            filled = int(round(score / 10))
            bar = "█" * filled + "░" * (10 - filled)
            add_row([f"{name} ({weight_pct}%)", score, bar])
        add_row([])


    # ── 3.5 Portfolio Management & Source ────────
    if "source_summary" in dash:
        add_row(["Portfolio Management Summary"], is_header=True)
        ss = dash["source_summary"]
        add_row(["Metric", "Value"], is_subheader=True)
        add_row(["Total Holdings", dash["num_holdings"]])
        add_row(["Self-Managed Holdings", ss.get("SELF", {}).get("count", 0)])
        add_row(["Smallcase Holdings", ss.get("SMALLCASE", {}).get("count", 0)])
        add_row(["ETF Holdings", ss.get("ETF", {}).get("count", 0)])
        add_row(["Other / Legacy", ss.get("LEGACY", {}).get("count", 0) + ss.get("UNKNOWN", {}).get("count", 0)])
        val = dash.get("portfolio_beta")
        cov = dash.get("beta_covered_pct")
        cov_str = f", {cov}% coverage" if cov is not None else ""
        beta_label = f"{val} (vs NIFTY 50{cov_str})" if val is not None else "N/A"
        add_row(["Portfolio Beta", beta_label])
        val = dash.get("portfolio_xirr")
        idx = add_row(["Portfolio XIRR", val if val is not None else "N/A"])
        if val is not None:
            pct_cells.append((idx, 1))
            pos_neg_cells.append((idx, 1, val))
        add_row([])

        add_row(["Investment Source Comparison"], is_header=True)
        add_row(["Source", "Holdings", "Invested", "Current Value", "P&L", "Return %"], is_subheader=True)
        for src in ["SELF", "SMALLCASE", "ETF", "LEGACY"]:
            agg = ss.get(src, {"count": 0, "invested": 0.0, "value": 0.0})
            if agg["count"] == 0 and src == "LEGACY":
                # Check unknown
                agg = ss.get("UNKNOWN", {"count": 0, "invested": 0.0, "value": 0.0})
                if agg["count"] == 0: continue
            
            s_pnl = round(agg["value"] - agg["invested"], 2)
            s_ret = round((s_pnl / agg["invested"]) * 100, 2) if agg["invested"] else None
            r_idx = add_row([src, agg["count"], round(agg["invested"], 2), round(agg["value"], 2), s_pnl, s_ret if s_ret is not None else "N/A"])
            currency_cells.append((r_idx, 2))
            currency_cells.append((r_idx, 3))
            currency_cells.append((r_idx, 4))
            pos_neg_cells.append((r_idx, 4, s_pnl))
            if s_ret is not None:
                pct_cells.append((r_idx, 5))
                pos_neg_cells.append((r_idx, 5, s_ret))
        add_row([])

    # ── 4. Portfolio allocation: Top 5 holdings + Others ──
    if dash.get("positions"):
        add_row(["Top Holdings"], is_header=True)
        add_row(["Symbol", "Value", "% of Portfolio"], is_subheader=True)
        top5 = dash["positions"][:5]
        rest = dash["positions"][5:]
        for sym, val, pct, flag in top5:
            r_idx = add_row([sym, val, pct if pct else 0.0, flag])
            currency_cells.append((r_idx, 1))
            pct_cells.append((r_idx, 2))
        if rest:
            other_val = round(sum(p[1] for p in rest), 2)
            other_pct = round(sum(p[2] for p in rest), 2)
            r_idx = add_row([f"Others ({len(rest)})", other_val, other_pct if other_pct else 0.0])
            currency_cells.append((r_idx, 1))
            pct_cells.append((r_idx, 2))
        add_row([])

    # ── 5. Sector Allocation (holdings count, weight%, invested,
    #    value, P&L, return%, concentration flag per sector) +
    #    horizontal bar chart ──
    sector_detail = dash.get("sector_detail")
    if sector_detail:
        add_row(["Sector Allocation"], is_header=True)
        add_row(["Sector", "Holdings", "Weight %", "Invested", "Value", "P&L", "Return %", "Beta", "Flag"], is_subheader=True)
        first_data_row = len(rows) + 1
        for s in sector_detail:
            row = [
                s["sector"], s["count"], s["weight_pct"],
                s["invested"], s["value"], s["pnl"],
                s["return_pct"] if s["return_pct"] is not None else "N/A",
                s["beta"] if s["beta"] is not None else "N/A",
                s.get("flag", ""),
            ]
            r_idx = add_row(row)
            pct_cells.append((r_idx, 2))
            currency_cells.append((r_idx, 3))
            currency_cells.append((r_idx, 4))
            currency_cells.append((r_idx, 5))
            pos_neg_cells.append((r_idx, 5, s["pnl"]))
            if s["return_pct"] is not None:
                pct_cells.append((r_idx, 6))
                pos_neg_cells.append((r_idx, 6, s["return_pct"]))
        last_data_row = len(rows)
        sector_table_rows = (first_data_row, last_data_row)
        add_row([])
    elif dash.get("sector_alloc"):
        # combined_rows wasn't supplied — fall back to the value/weight-only
        # view rather than fabricating per-sector P&L.
        add_row(["Sector Allocation"], is_header=True)
        add_row(["Sector", "Value", "% of Portfolio"], is_subheader=True)
        for sector, val, pct in dash["sector_alloc"]:
            r_idx = add_row([sector, val, pct if pct else 0.0])
            currency_cells.append((r_idx, 1))
            pct_cells.append((r_idx, 2))
        add_row([])


        add_row(["Top Positive Sector Contributors", "P&L"], is_subheader=True)
        if dash.get("top_positive_sectors"):
            for s in dash["top_positive_sectors"]:
                r_idx = add_row([s["sector"], s["pnl"]])
                currency_cells.append((r_idx, 1))
                pos_neg_cells.append((r_idx, 1, s["pnl"]))
        else:
            add_row(["(none)"])
        add_row([])

        add_row(["Top Negative Sector Contributors", "P&L"], is_subheader=True)
        if dash.get("top_negative_sectors"):
            for s in dash["top_negative_sectors"]:
                r_idx = add_row([s["sector"], s["pnl"]])
                currency_cells.append((r_idx, 1))
                pos_neg_cells.append((r_idx, 1, s["pnl"]))
        else:
            add_row(["(none)"])
        add_row([])

    # ── 6. Top Gainers / Top Losers ───────────
    if has_kpis:
        add_row(["Top Gainers", "P&L", "Return %"], is_subheader=True)
        if dash["top_gainers"]:
            for r in dash["top_gainers"]:
                r_idx = add_row([r["symbol"], r["pnl"], r["return_pct"]])
                currency_cells.append((r_idx, 1))
                pct_cells.append((r_idx, 2))
                pos_neg_cells.append((r_idx, 1, r["pnl"]))
                pos_neg_cells.append((r_idx, 2, r["return_pct"]))
        else:
            add_row(["(none)"])
        add_row([])

        add_row(["Top Losers", "P&L", "Return %"], is_subheader=True)
        if dash["top_losers"]:
            for r in dash["top_losers"]:
                r_idx = add_row([r["symbol"], r["pnl"], r["return_pct"]])
                currency_cells.append((r_idx, 1))
                pct_cells.append((r_idx, 2))
                pos_neg_cells.append((r_idx, 1, r["pnl"]))
                pos_neg_cells.append((r_idx, 2, r["return_pct"]))
        else:
            add_row(["(none)"])
        add_row([])


        add_row(["Top Positive Portfolio Contributors", "Weight %", "Return %", "P&L", "Impact %"], is_subheader=True)
        if dash.get("top_positive_impact"):
            for r in dash["top_positive_impact"]:
                r_idx = add_row([r["symbol"], r["wt_pct"], r["return_pct"], r["pnl"], r["portfolio_impact"]])
                pct_cells.append((r_idx, 1))
                pct_cells.append((r_idx, 2))
                currency_cells.append((r_idx, 3))
                pct_cells.append((r_idx, 4))
                pos_neg_cells.append((r_idx, 2, r["return_pct"]))
                pos_neg_cells.append((r_idx, 3, r["pnl"]))
                pos_neg_cells.append((r_idx, 4, r["portfolio_impact"]))
        else:
            add_row(["(none)"])
        add_row([])

        add_row(["Top Negative Portfolio Contributors", "Weight %", "Return %", "P&L", "Impact %"], is_subheader=True)
        if dash.get("top_negative_impact"):
            for r in dash["top_negative_impact"]:
                r_idx = add_row([r["symbol"], r["wt_pct"], r["return_pct"], r["pnl"], r["portfolio_impact"]])
                pct_cells.append((r_idx, 1))
                pct_cells.append((r_idx, 2))
                currency_cells.append((r_idx, 3))
                pct_cells.append((r_idx, 4))
                pos_neg_cells.append((r_idx, 2, r["return_pct"]))
                pos_neg_cells.append((r_idx, 3, r["pnl"]))
                pos_neg_cells.append((r_idx, 4, r["portfolio_impact"]))
        else:
            add_row(["(none)"])
        add_row([])

        # ── 7. Portfolio Signals + Action Required ──
        add_row(["Portfolio Signals"], is_header=True)
        sc = dash["signal_counts"]
        signal_summary_row = [
            f"HOLD: {sc.get('HOLD', 0)}",
            f"BUY MORE@: {sc.get('BUY MORE', 0)}",
            f"SELL - SL HIT: {sc.get('SELL - SL HIT', 0)}",
            f"TARGET HIT: {sc.get('TARGET HIT - TRIM', 0)}",
        ]
        add_row(signal_summary_row, is_subheader=True)
        add_row([])

        add_row(["Action Required", "Signal", "P&L", "Return %"], is_subheader=True)
        if dash["action_required"]:
            for r in dash["action_required"]:
                sym_lbl = r.get("symbol_display", r.get("symbol", ""))
                r_idx = add_row([sym_lbl, r["signal"], r["pnl"], r["return_pct"]])
                currency_cells.append((r_idx, 2))
                pct_cells.append((r_idx, 3))
                signal_cells.append((r_idx, 1, r["signal"]))
        else:
            add_row(["(none — all holdings within Hold range)"])
        add_row([])

        # ── 8. Concentration / Summary ────────────
        add_row(["Portfolio Summary"], is_header=True)
        add_row(["Number of Holdings", dash["num_holdings"]])
        if dash.get("largest_holding"):
            lh = dash["largest_holding"]
            add_row(["Largest Holding", f"{lh[0]} ({lh[2]}%)"])
        add_row(["Top 5 Holdings Combined Weight", f"{dash['top5_weight']}%"])
        add_row(["Number of Sectors", dash.get("num_sectors", "N/A")])
        if dash.get("sector_alloc"):
            top_sector = dash["sector_alloc"][0]
            add_row(["Largest Sector", f"{top_sector[0]} ({top_sector[2]}%)"])
        add_row(["Top 3 Sectors Combined Weight", f"{dash.get('top3_sector_weight', 'N/A')}%"])

        # Short-term momentum summary
        if dash.get("momentum_1w") and (dash["momentum_1w"][0] > 0 or dash["momentum_1w"][1] > 0):
            up_w, dn_w = dash["momentum_1w"]
            add_row(["1W Momentum (Holdings)", f"▲ {up_w} Up  /  ▼ {dn_w} Down"])
        if dash.get("momentum_1m") and (dash["momentum_1m"][0] > 0 or dash["momentum_1m"][1] > 0):
            up_m, dn_m = dash["momentum_1m"]
            add_row(["1M Momentum (Holdings)", f"▲ {up_m} Up  /  ▼ {dn_m} Down"])

        val = dash["portfolio_xirr"]
        if val is not None:
            idx = add_row(["Portfolio XIRR", val])
            pct_cells.append((idx, 1))
            pos_neg_cells.append((idx, 1, val))
        val = dash["portfolio_beta"]
        cov = dash.get("beta_covered_pct")
        cov_str = f", {cov}% coverage" if cov is not None else ""
        beta_label = f"{val} (vs NIFTY 50{cov_str})" if val is not None else "N/A"
        add_row(["Portfolio Beta", beta_label])
        idx = add_row(["Expected Div Income (annual)", dash['div_income']])
        currency_cells.append((idx, 1))
        add_row([])
    else:
        # combined_rows wasn't supplied — fall back to the beta/XIRR/div-only
        # KPI set rather than fabricating P&L/Return%/gainers/losers.
        add_row(["Portfolio KPIs"], is_header=True)
        val = dash["portfolio_xirr"]
        if val is not None:
            idx = add_row(["Portfolio XIRR", val])
            pct_cells.append((idx, 1))
            pos_neg_cells.append((idx, 1, val))
        else:
            add_row(["Portfolio XIRR%", "N/A"])
        val = dash["portfolio_beta"]
        cov = dash.get("beta_covered_pct")
        cov_str = f", {cov}% coverage" if cov is not None else ""
        beta_label = f"{val} (vs NIFTY 50{cov_str})" if val is not None else "N/A"
        add_row(["Portfolio Beta", beta_label])
        idx = add_row(["Expected Div Income (annual)", dash['div_income']])
        currency_cells.append((idx, 1))
        add_row([])

    # ── 9. Today's Changes (unchanged behaviour) ──
    if changes and changes.get("prev_date"):
        add_row([f"Today's Changes (vs {changes['prev_date']})"], is_header=True)
        add_row([])

        add_row(["Top Improvements", "Score Delta", "Action Change", "Priority"], is_subheader=True)
        for i, c in enumerate(changes["top_improvements"], 1):
            r_idx = add_row([f"{i}. {c['symbol']}", c['score_delta'], f"{c['prev_action']} → {c['today_action']}", c["priority"]])
            pos_neg_cells.append((r_idx, 1, c['score_delta']))
            add_row(["", f"Reason: {c['reason']}"])
            add_row(["", f"Why: {c['why']}"])
        if not changes["top_improvements"]:
            add_row(["(none)"])

        add_row([])
        add_row(["Top Deteriorations", "Score Delta", "Action Change", "Priority"], is_subheader=True)
        for i, c in enumerate(changes["top_deteriorations"], 1):
            r_idx = add_row([f"{i}. {c['symbol']}", c['score_delta'], f"{c['prev_action']} → {c['today_action']}", c["priority"]])
            pos_neg_cells.append((r_idx, 1, c['score_delta']))
            add_row(["", f"Reason: {c['reason']}"])
            add_row(["", f"Why: {c['why']}"])
        if not changes["top_deteriorations"]:
            add_row(["(none)"])

        add_row([])
        add_row([f"Unchanged Holdings: {changes['unchanged_count']}"])
    elif changes is not None:
        add_row(["Today's Changes", "No prior trading day in History yet - check back tomorrow"], is_header=True)

    ws.update("A1", rows, value_input_option="RAW")

    widths = [220, 110, 100, 130, 130, 120, 100, 100, 120]
    reqs = sheet_formatter.clear_all_formatting_reqs(ws.id) + sheet_formatter.get_structural_format_reqs(
        ws.id, len(rows), nc, widths=widths, freeze_rows=0, freeze_cols=0)

    # NOTE: color_cell_req / color_positive_negative / color_action_signal
    # all take a 0-indexed API row. add_row() above returns a 1-indexed
    # sheet row number, so every call below passes (idx - 1).
    for h_idx in header_indices:
        for col in range(nc):
            reqs.append(sheet_formatter.color_cell_req(ws.id, h_idx - 1, col, "0d1b2a", "ffffff", font_size=8))

    for s_idx in subheader_indices:
        for col in range(nc):
            reqs.append(sheet_formatter.color_cell_req(ws.id, s_idx - 1, col, "1c3144", "ffffff", font_size=8))

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
        req = sheet_formatter.color_positive_negative(ws.id, r_idx - 1, c_idx, val)
        if req: reqs.append(req)

    for r_idx, c_idx in currency_cells:
        reqs += sheet_formatter.get_currency_format_reqs(ws.id, r_idx - 1, r_idx, c_idx, c_idx + 1)

    for r_idx, c_idx in pct_cells:
        reqs += sheet_formatter.get_percentage_format_reqs(ws.id, r_idx - 1, r_idx, c_idx, c_idx + 1)

    # Action Required signal cells get the same subtle colour coding
    # used on the Portfolio tab (soft red/blue/green), for consistency.
    for r_idx, c_idx, sig in signal_cells:
        req = sheet_formatter.color_action_signal(ws.id, r_idx - 1, c_idx, sig)
        if req: reqs.append(req)

    if reqs:
        sheet_writer.batch_update_safe(sh, reqs)

    # ── Sector allocation bar chart (separate batch call: addChart
    #    needs the cell values above already committed) ──────
    if sector_table_rows:
        first_row, last_row = sector_table_rows  # 1-indexed, inclusive
        chart_req = {
            "addChart": {
                "chart": {
                    "spec": {
                        "title": "Sector Allocation — Weight %",
                        "basicChart": {
                            "chartType": "BAR",
                            "legendPosition": "NO_LEGEND",
                            "axis": [
                                {"position": "BOTTOM_AXIS", "title": "Weight %"},
                                {"position": "LEFT_AXIS", "title": "Sector"},
                            ],
                            "domains": [{
                                "domain": {"sourceRange": {"sources": [{
                                    "sheetId": ws.id, "startRowIndex": first_row - 1, "endRowIndex": last_row,
                                    "startColumnIndex": 0, "endColumnIndex": 1,
                                }]}}
                            }],
                            "series": [{
                                "series": {"sourceRange": {"sources": [{
                                    "sheetId": ws.id, "startRowIndex": first_row - 1, "endRowIndex": last_row,
                                    "startColumnIndex": 2, "endColumnIndex": 3,
                                }]}},
                                "targetAxis": "BOTTOM_AXIS",
                            }],
                            "headerCount": 0,
                        },
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {"sheetId": ws.id, "rowIndex": first_row - 1, "columnIndex": nc + 1},
                            "widthPixels": 480, "heightPixels": 320,
                        }
                    },
                }
            }
        }
        try:
            sheet_writer.batch_update_safe(sh, [chart_req])
        except Exception as e:
            log.warning(f"Sector allocation chart insert failed (non-fatal): {e}")

    log.info("Dashboard tab written with enhanced formatting")
    return ws
