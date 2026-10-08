"""
morning_brief_builder.py - builds the 8:00 AM IST morning brief
(price alerts + rule checks + news) as one Telegram HTML message.

Holdings come from the same pipeline as the Portfolio tab
(portfolio_builder.load_all_trades -> build_portfolio). That pipeline already
merges duplicate lots per symbol, so weights and stop-loss levels are not
distorted by duplicate rows.

Prices are the last NSE close: this runs before the 09:15 IST open, and on
weekends/holidays the last close is the latest data there is.

This module never writes to a worksheet. (fund_cache may refresh its own
fundamentals cache for stale symbols, same as morning_buying_zone.py.)
"""
import html
import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import yfinance as yf

import fund_cache
import portfolio_builder
from config import NEWS_WORKERS, TECH_WORKERS
from data_fetcher import fetch_prices_batch, fetch_technicals
from news_engine import classifier
from news_engine.sources import google_news_rss

log = logging.getLogger("morning_brief")

IST = timezone(timedelta(hours=5, minutes=30))

# ── Thresholds (approved) ────────────────────────────────────────────────────
SL_NEAR_PCT = 2.0          # alert when CMP is within 2% above the stop-loss
MOVE_ALERT_PCT = 5.0       # alert on a >=5% move vs previous close
MAX_POSITION_PCT = 7.0     # standing rule: 7% max position size
SECTOR_CAP_PCT = 30.0      # standing rule: 30% sector cap
MAX_TRANCHES = 3           # standing rule: 2-3 tranche maximum

# ── News settings ────────────────────────────────────────────────────────────
NEWS_LOOKBACK_HOURS = 36
MAX_NEWS_SYMBOLS = 12
MAX_HEADLINES = 2
MATERIAL_CATEGORIES = {
    "earnings", "management", "corporate_actions", "promoter_activity",
    "order_wins", "approvals", "regulatory", "acquisitions", "dividends",
}


def esc(v):
    return html.escape("" if v is None else str(v))


def _num(v):
    """float if v is a real number, else None ('' and NaN both give None)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:
        return float(v)
    return None


# ══════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════
def fetch_tech_map(symbols):
    out = {}
    with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
        futs = {ex.submit(fetch_technicals, s): s for s in symbols}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                out[s] = fut.result() or {}
            except Exception as e:
                log.warning(f"[brief] technicals failed for {s}: {e}")
                out[s] = {}
    return out


def load_portfolio(sh):
    """Returns (rows, trades, tech_map, fund_map). rows = one dict per symbol."""
    trades = portfolio_builder.load_all_trades()
    held = portfolio_builder.compute_holdings(trades)
    symbols = sorted({h["symbol"] for h in held.values()})
    log.info(f"[brief] {len(symbols)} held symbols across brokers")

    prices = fetch_prices_batch(symbols)
    tech_map = fetch_tech_map(symbols)

    try:
        fc_cache = fund_cache.load_cache(sh)
    except Exception as e:
        log.warning(f"[brief] could not load fund_cache: {e}")
        fc_cache = {}
    fund_map = {}
    for s in symbols:
        try:
            fund_map[s] = fund_cache.get_or_fetch_fundamentals(s, fc_cache, max_age_days=7) or {}
        except Exception as e:
            log.warning(f"[brief] fundamentals failed for {s}: {e}")
            fund_map[s] = {}

    result = portfolio_builder.build_portfolio(
        prices, trades=trades, fund_map=fund_map, tech_map=tech_map
    )
    return result["combined"], trades, tech_map, fund_map


def count_tranches(trades, symbols):
    """Distinct BUY dates per symbol (approximation of 'tranches')."""
    dates = defaultdict(set)
    for t in trades:
        sym = str(t.get("symbol", "")).strip().upper()
        if sym not in symbols:
            continue
        if str(t.get("action", "")).strip().upper() != "BUY":
            continue
        d = portfolio_builder._parse_trade_date_iso(t.get("date", ""))
        if d:
            dates[sym].add(d)
    return {s: len(v) for s, v in dates.items()}


def get_nifty():
    """(last_close, pct_change_vs_prev_close, last_close_date) or (None, None, None)."""
    try:
        df = yf.Ticker("^NSEI").history(period="5d")
        if df is None or len(df) < 2:
            return None, None, None
        last = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2])
        pct = (last - prev) / prev * 100 if prev else 0.0
        return last, pct, df.index[-1].date()
    except Exception as e:
        log.warning(f"[brief] NIFTY fetch failed: {e}")
        return None, None, None


# ══════════════════════════════════════════════
# ALERTS + RULE CHECKS (pure functions)
# ══════════════════════════════════════════════
def build_alerts(rows, tech_map):
    a = {"sl_breach": [], "sl_near": [], "target": [], "movers": []}
    for r in rows:
        sym = r["symbol"]
        cmp = _num(r.get("cmp"))
        if cmp is None:
            continue
        sl = _num(r.get("sl_price"))     # '' for ETFs -> None -> exempt
        tgt = _num(r.get("target"))
        ret = _num(r.get("return_pct"))
        day = _num((tech_map.get(sym) or {}).get("day_chg_pct"))

        if sl is not None:
            if cmp <= sl:
                a["sl_breach"].append({"sym": sym, "cmp": cmp, "sl": sl, "ret": ret})
            elif cmp <= sl * (1 + SL_NEAR_PCT / 100):
                a["sl_near"].append({"sym": sym, "cmp": cmp, "sl": sl,
                                     "gap": (cmp / sl - 1) * 100})
        if tgt is not None and cmp >= tgt:
            a["target"].append({"sym": sym, "cmp": cmp, "tgt": tgt, "ret": ret})
        if day is not None and abs(day) >= MOVE_ALERT_PCT:
            a["movers"].append({"sym": sym, "cmp": cmp, "day": day})

    a["sl_breach"].sort(key=lambda x: (x["ret"] if x["ret"] is not None else 0))
    a["sl_near"].sort(key=lambda x: x["gap"])
    a["target"].sort(key=lambda x: -(x["ret"] if x["ret"] is not None else 0))
    a["movers"].sort(key=lambda x: -abs(x["day"]))
    return a


def rule_checks(rows, fund_map, tranches):
    """7% position, 30% sector, tranche limit. ETFs are exempt (same as stop-loss)."""
    out = {"position": [], "sector": [], "tranche": []}
    total = sum(_num(r.get("value")) or 0.0 for r in rows)
    sector_val = defaultdict(float)

    for r in rows:
        sym = r["symbol"]
        if r.get("investment_source") == "ETF":
            continue
        wt = _num(r.get("wt_pct"))
        if wt is not None and wt > MAX_POSITION_PCT:
            out["position"].append({"sym": sym, "wt": wt})
        sector = (fund_map.get(sym) or {}).get("sector") or ""
        if sector and sector != "ETFs":
            sector_val[sector] += _num(r.get("value")) or 0.0
        n = tranches.get(sym, 0)
        if n > MAX_TRANCHES:
            out["tranche"].append({"sym": sym, "n": n})

    if total > 0:
        for sec, v in sector_val.items():
            pct = v / total * 100
            if pct > SECTOR_CAP_PCT:
                out["sector"].append({"sector": sec, "pct": pct})

    out["position"].sort(key=lambda x: -x["wt"])
    out["sector"].sort(key=lambda x: -x["pct"])
    out["tranche"].sort(key=lambda x: -x["n"])
    return out


# ══════════════════════════════════════════════
# NEWS
# ══════════════════════════════════════════════
def _pub_dt(s):
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _news_for_symbol(sym, name, cutoff):
    try:
        articles = google_news_rss.fetch(sym, name)
    except Exception as e:
        log.warning(f"[brief] news fetch failed for {sym}: {e}")
        return None
    fresh = []
    for art in articles:
        d = _pub_dt(art.get("published", ""))
        if d is not None and d >= cutoff:
            fresh.append(art)
    if not fresh:
        return None
    result, enriched = classifier.classify(sym, fresh)
    material = [
        art for art in enriched
        if art.get("bullish_keywords") or art.get("bearish_keywords")
        or art.get("matched_category") in MATERIAL_CATEGORIES
    ]
    if not material:
        return None
    return {
        "sym": sym,
        "sentiment": result.sentiment,
        "score": result.bullish_score + result.bearish_score,
        "items": material[:MAX_HEADLINES],
    }


def fetch_news(rows, fund_map, now_utc):
    cutoff = now_utc - timedelta(hours=NEWS_LOOKBACK_HOURS)
    jobs = {}
    with ThreadPoolExecutor(max_workers=NEWS_WORKERS) as ex:
        for r in rows:
            sym = r["symbol"]
            name = (fund_map.get(sym) or {}).get("shortName") or sym
            jobs[ex.submit(_news_for_symbol, sym, name, cutoff)] = sym
        found = []
        for fut in as_completed(jobs):
            try:
                res = fut.result()
            except Exception as e:
                log.warning(f"[brief] news job failed for {jobs[fut]}: {e}")
                continue
            if res:
                found.append(res)
    order = {"Bearish": 0, "Bullish": 1, "Neutral": 2}
    found.sort(key=lambda x: (order.get(x["sentiment"], 2), -x["score"]))
    return found


# ══════════════════════════════════════════════
# MESSAGE
# ══════════════════════════════════════════════
def format_message(now_ist, nifty, alerts, rules, news, n_holdings):
    val, pct, close_date = nifty
    msg = "🌅 <b>MORNING BRIEF</b>\n"
    msg += f"<i>{now_ist.strftime('%a %d-%b-%Y | %I:%M %p IST')}</i>\n"
    if val is not None:
        sign = "+" if pct >= 0 else ""
        msg += f"<b>NIFTY 50:</b> {val:,.2f} ({sign}{pct:.2f}%) · close of {close_date.strftime('%d-%b')}\n"
    msg += f"<i>{n_holdings} holdings · prices are last close</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    any_price_alert = False

    if alerts["sl_breach"]:
        any_price_alert = True
        msg += f"<b>🔴 STOP-LOSS BREACHED ({len(alerts['sl_breach'])})</b>\n"
        for x in alerts["sl_breach"][:8]:
            ret = f" | {x['ret']:+.1f}%" if x["ret"] is not None else ""
            msg += f"  • <b>{esc(x['sym'])}</b>: ₹{x['cmp']:,.2f} (SL ₹{x['sl']:,.2f}){ret}\n"
        msg += "\n"

    if alerts["sl_near"]:
        any_price_alert = True
        msg += f"<b>⚠️ NEAR STOP-LOSS, within {SL_NEAR_PCT:.0f}% ({len(alerts['sl_near'])})</b>\n"
        for x in alerts["sl_near"][:8]:
            msg += f"  • <b>{esc(x['sym'])}</b>: ₹{x['cmp']:,.2f} (SL ₹{x['sl']:,.2f}, {x['gap']:.1f}% above)\n"
        msg += "\n"

    if alerts["target"]:
        any_price_alert = True
        msg += f"<b>🎯 TARGET HIT ({len(alerts['target'])})</b>\n"
        for x in alerts["target"][:8]:
            ret = f" | {x['ret']:+.1f}%" if x["ret"] is not None else ""
            msg += f"  • <b>{esc(x['sym'])}</b>: ₹{x['cmp']:,.2f} (Tgt ₹{x['tgt']:,.2f}){ret}\n"
        msg += "\n"

    if alerts["movers"]:
        any_price_alert = True
        msg += f"<b>📊 BIG MOVERS, ≥{MOVE_ALERT_PCT:.0f}% last session ({len(alerts['movers'])})</b>\n"
        for x in alerts["movers"][:8]:
            msg += f"  • <b>{esc(x['sym'])}</b>: {x['day']:+.2f}% (₹{x['cmp']:,.2f})\n"
        msg += "\n"

    if not any_price_alert:
        msg += "✅ <b>No price alerts</b>\n\n"

    msg += "<b>⚖️ RULE CHECKS</b>\n"
    if not (rules["position"] or rules["sector"] or rules["tranche"]):
        msg += "  ✅ All within limits\n"
    for x in rules["position"][:8]:
        msg += f"  • Position &gt;{MAX_POSITION_PCT:.0f}%: <b>{esc(x['sym'])}</b> at {x['wt']:.1f}%\n"
    for x in rules["sector"]:
        msg += f"  • Sector &gt;{SECTOR_CAP_PCT:.0f}%: <b>{esc(x['sector'])}</b> at {x['pct']:.1f}%\n"
    for x in rules["tranche"][:8]:
        msg += f"  • Tranches &gt;{MAX_TRANCHES}: <b>{esc(x['sym'])}</b> has {x['n']} buy dates\n"
    msg += "\n"

    msg += f"<b>📰 NEWS (last {NEWS_LOOKBACK_HOURS}h, holdings only)</b>\n"
    if not news:
        msg += "  No material headlines\n"
    icon = {"Bearish": "🔴", "Bullish": "🟢", "Neutral": "⚪"}
    for n in news[:MAX_NEWS_SYMBOLS]:
        msg += f"{icon.get(n['sentiment'], '⚪')} <b>{esc(n['sym'])}</b> ({esc(n['sentiment'])})\n"
        for art in n["items"]:
            src = f" - {esc(art['source'])}" if art.get("source") else ""
            msg += f"    · {esc(art['title'][:140])}{src}\n"
    if len(news) > MAX_NEWS_SYMBOLS:
        msg += f"  <i>...and {len(news) - MAX_NEWS_SYMBOLS} more symbols with news</i>\n"

    msg += "\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += "<i>Keyword-based news screening and rule checks. Decision support, not advice.</i>"
    return msg


def build_brief(sh, now_ist=None):
    now_ist = now_ist or datetime.now(IST)
    rows, trades, tech_map, fund_map = load_portfolio(sh)
    if not rows:
        raise RuntimeError("No holdings loaded - check data/imports and price fetch")

    symbols = {r["symbol"] for r in rows}
    alerts = build_alerts(rows, tech_map)
    rules = rule_checks(rows, fund_map, count_tranches(trades, symbols))
    news = fetch_news(rows, fund_map, now_ist.astimezone(timezone.utc))
    nifty = get_nifty()
    log.info(
        f"[brief] alerts: SL {len(alerts['sl_breach'])}, near {len(alerts['sl_near'])}, "
        f"target {len(alerts['target'])}, movers {len(alerts['movers'])}; "
        f"rules: pos {len(rules['position'])}, sector {len(rules['sector'])}, "
        f"tranche {len(rules['tranche'])}; news symbols {len(news)}"
    )
    return format_message(now_ist, nifty, alerts, rules, news, len(rows))
