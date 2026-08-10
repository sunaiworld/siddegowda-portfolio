from config import *

from score_engine import *
from sheet_writer import *
from sheet_formatter import *
from github_data_builder import *
from growth_screener_builder import *
from future_buy_builder import *
from data_fetcher import *
from telegram_alerts import *
from portfolio_builder import *
import mutual_fund_builder

#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Daily Auto-Updater
Sector-Aware Unified Scoring Engine v2.0
GitHub Actions — runs daily 6 PM IST
"""

import os

import json

import time

import logging

import statistics

import requests

import math

from datetime import datetime, date, timezone

from concurrent.futures import ThreadPoolExecutor, as_completed


import numpy as np

import pandas as pd

import yfinance as yf

import gspread

from google.oauth2.service_account import Credentials

import fund_cache

import history_tracker

import portfolio_analytics

import news_engine.news_cache as news_cache

from news_engine.sources import google_news_rss

from news_engine import classifier


# ══════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════

# ══════════════════════════════════════════════
# WATCHLISTS
# Symbol-only categories: no qty/buy-price/date.
# Displayed as market-data-only tabs (reuse GITHUB DATA
# tab logic). Add new watchlist categories here instead
# of hardcoding values elsewhere.
# ══════════════════════════════════════════════


# ══════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════
def run_portfolio_update(sh):
    """
    Runs the full daily pipeline: fetch prices/fundamentals/technicals,
    score every symbol, write GITHUB DATA + Growth Screener + all
    WATCHLISTS tabs. Returns everything the caller needs (GitHub Actions
    cron AND the /refresh bot command both call this — single source
    of truth, no duplicated pipeline logic).
    """
    symbols = read_symbols(sh)
    if not symbols:
        log.error("No symbols found.")
        return None

    # Real trades live in data/imports/{zerodha,groww} now, not the legacy
    # "Trade Log" sheet tab (which is empty/unused) — load_all_trades()
    # reads both broker exports via the existing import_zerodha.py /
    # import_groww.py importers, and trades_to_legacy_rows() adapts them
    # into the row shape get_avg_buy_and_qty/get_xirr/get_entry_date
    # already expect, so nothing downstream (Dashboard holdings dict,
    # SL/target alerts, per-symbol XIRR) needs to change.
    trades = trades_to_legacy_rows(load_all_trades())
    log.info(f"Found {len(symbols)} symbols")

    log.info("Fetching prices...")
    prices = fetch_prices_batch(symbols)

    log.info("Fetching fundamentals...")
    fund_map = {}
    fc_cache = fund_cache.load_cache(sh)
    
    def _fetch_fund(sym):
        return sym, fund_cache.get_or_fetch_fundamentals(sym, fc_cache, max_age_days=FUNDAMENTALS_CACHE_DAYS)

    with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
        futures = {ex.submit(_fetch_fund, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                _, data = fut.result()
                fund_map[sym] = data
            except Exception as e:
                log.warning(f"  fundamentals failed {sym}: {e}")
                fund_map[sym] = {}
                
    fund_cache.save_cache(sh, fc_cache)

    # Technicals + revenue growth: both hit yfinance directly (no cache layer
    # like fundamentals has), so this is the biggest per-symbol serial cost
    # in the pipeline. Bounded ThreadPoolExecutor — same host as prices, so
    # TECH_WORKERS stays conservative to respect Yahoo's rate limits.
    # fetch_technicals()/fetch_rev_growth() themselves are unchanged; only
    # the loop that calls them is now parallel.
    log.info(f"Fetching technicals + revenue growth ({TECH_WORKERS} workers)...")
    tech_map, rev_map = {}, {}

    def _fetch_tech_and_growth(sym):
        return sym, fetch_technicals(sym), fetch_rev_growth(sym)

    with ThreadPoolExecutor(max_workers=TECH_WORKERS) as ex:
        futures = {ex.submit(_fetch_tech_and_growth, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                _, tech, rev_gr = fut.result()
            except Exception as e:
                log.warning(f"  tech/growth failed {sym}: {e}")
                tech, rev_gr = {}, None
            tech_map[sym] = tech
            rev_map[sym]  = rev_gr
            log.info(f"  Technicals: {sym}")

    # ── News Engine: load cached news for all symbols (Phase A) ──
    try:
        nc_cache, nc_row_map, nc_ws = news_cache.load(sh)
        log.info(f"[news_cache] Loaded news for {len(nc_cache)} symbols")
    except Exception as _e:
        log.warning(f"[news_cache] Could not load news cache, skipping: {_e}")
        nc_cache, nc_row_map, nc_ws = {}, {}, None
    pending_news = {}

    # Build holdings for every symbol with active broker holdings — not only
    # watchlist symbols.  This ensures a stock first bought in a new broker
    # import file (not yet in the Portfolio sheet's col B) is captured in
    # the holdings dict and written to Portfolio on the next run.
    from portfolio_builder import compute_holdings
    raw_held = compute_holdings(load_all_trades())
    
    all_held = {}
    for key, h in raw_held.items():
        sym = h["symbol"]
        if sym not in all_held:
            all_held[sym] = {"qty": 0.0, "cost": 0.0}
        all_held[sym]["qty"] += h["qty"]
        all_held[sym]["cost"] += h["cost"]
        
    for sym in all_held:
        q = all_held[sym]["qty"]
        c = all_held[sym]["cost"]
        all_held[sym] = (c / q if q else 0.0, q, c)

    extra_syms = [s for s in all_held if s not in set(symbols)]
    if extra_syms:
        log.info(f"Fetching prices for {len(extra_syms)} holdings-only symbol(s): {extra_syms}")
        extra_prices = fetch_prices_batch(extra_syms)
        prices.update(extra_prices)

    holdings, portfolio_live_value = {}, 0.0
    for sym in list(symbols) + extra_syms:
        if sym in all_held:
            avg_buy, qty, _cost = all_held[sym]
        else:
            avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        cmp = prices.get(sym)
        if qty > 0 and cmp and cmp > 0:
            holdings[sym] = (qty, cmp, avg_buy)
            portfolio_live_value += qty * cmp



    # ── News refresh pre-pass (threaded, bounded) ───────────────────────────────────────────────
    # Google News RSS is a different host than Yahoo/Sheets, so fetching it
    # in parallel doesn't compound either rate limit. Only the *fetch* is
    # threaded — classify() is pure CPU, and no Sheets call happens inside
    # a thread; every write goes through the single flush() below.
    def _is_fresh(nd):
        if not nd or not nd.get("last_fetched"):
            return False
        try:
            dt = datetime.fromisoformat(nd["last_fetched"])
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600 < 6
        except Exception:
            return False

    stale_syms = [s for s in symbols if prices.get(s) and not _is_fresh(nc_cache.get(s.upper(), {}))]

    def _fetch_news(sym):
        raw_articles = google_news_rss.fetch(sym, sym)
        result, enriched = classifier.classify(sym, raw_articles)
        return sym, result, enriched

    if stale_syms:
        log.info(f"Fetching news for {len(stale_syms)} stale symbols ({NEWS_WORKERS} workers)...")
        with ThreadPoolExecutor(max_workers=NEWS_WORKERS) as ex:
            futures = {ex.submit(_fetch_news, sym): sym for sym in stale_syms}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, result, enriched = fut.result()
                except Exception as e:
                    log.warning(f"  News fetch failed {sym}: {e}")
                    continue
                try:
                    news_cache.stage_upsert(
                        pending_news, sym, result.timestamp, result.summary, enriched,
                        result.bullish_score, result.bearish_score,
                        result.sentiment, result.reason, result.source
                    )
                    nc_cache[sym.upper()] = {
                        "last_fetched": result.timestamp,
                        "digest": result.summary,
                        "raw_articles": enriched,
                        "bullish_score": result.bullish_score,
                        "bearish_score": result.bearish_score,
                        "sentiment": result.sentiment,
                        "reason": result.reason,
                        "source": result.source,
                    }
                except Exception as e:
                    log.warning(f"  News stage failed for {sym}: {e}")

    results, failed = [], []
    alerts = {"sl_breach": [], "target_hit": [], "strong_buy": [], "sell_watch": []}
    top_picks = []

    for sym in symbols:
        cmp = prices.get(sym)
        if not cmp:
            failed.append(sym)
            log.warning(f"  SKIP {sym} — no price")
            continue

        f, tech, rev_gr = fund_map.get(sym, {}), tech_map.get(sym, {}), rev_map.get(sym)
        avg_buy, qty = get_avg_buy_and_qty(sym, trades)
        xirr_val = get_xirr(sym, trades, cmp)
        nd = nc_cache.get(sym.upper(), {})

        try:
            row, archetype, tot_sc, final_action = build_result_row(sym, cmp, f, tech, rev_gr, xirr_val=xirr_val, news_data=nd)
        except Exception as e:
            log.error(f"[CHECKPOINT] build_result_row FAILED for {sym}: {e}", exc_info=True)
            raise
        log.info(f"[CHECKPOINT] Scoring {sym}")

        if avg_buy and qty > 0:
            sl_price, tgt_price = avg_buy * (1 - SL_PCT), avg_buy * (1 + TARGET_PCT)
            if cmp <= sl_price:
                alerts["sl_breach"].append({"sym": sym, "cmp": cmp, "sl": round(sl_price, 2)})
            if cmp >= tgt_price:
                alerts["target_hit"].append({"sym": sym, "cmp": cmp, "tgt": round(tgt_price, 2)})

        if final_action in ("STRONG BUY", "BUY"):
            alerts["strong_buy"].append({"sym": sym, "score": tot_sc, "action": final_action})
            top_picks.append({"sym": sym, "total": tot_sc, "action": final_action})
        elif final_action in ("AVOID", "SELL"):
            alerts["sell_watch"].append({"sym": sym, "score": tot_sc, "action": final_action})

        results.append(row)
        log.info(f"  {sym:12} | {archetype:25} | Total:{tot_sc:3} | {final_action}")

    top_picks.sort(key=lambda x: x["total"], reverse=True)

    if nc_ws is not None:
        news_cache.flush(nc_ws, nc_row_map, pending_news)
# Belt-and-suspenders: the scoring loop above is sequential over
    # `symbols`, so `results` is already in original order — but threading
    # was introduced upstream (technicals/rev-growth/news fetch), so this
    # guarantees row order in GITHUB DATA / Dashboard / History always
    # matches the Portfolio tab's symbol order, regardless of future edits
    # to how `results` gets assembled.
    _sym_index = {s: i for i, s in enumerate(symbols)}
    results.sort(key=lambda r: _sym_index.get(r[GITHUB_DATA_COLS["symbol"]], len(symbols)))
    write_github_data(sh, results, tab_name="GITHUB DATA")
    log.info(f"[CHECKPOINT] About to write GITHUB DATA — {len(results)} rows built")

    try:
        portfolio_rows = build_portfolio(prices)
        write_portfolio(sh, portfolio_rows)
    except Exception as e:
        log.error(f"[CHECKPOINT] Portfolio build/write FAILED: {e}", exc_info=True)
        raise

    # Pass nc_cache so watchlist symbols inherit the news signal that was
    # already fetched/refreshed for portfolio symbols above. Zero new
    # API calls for symbols that overlap; watchlist-only symbols get {}
    # and their news columns stay blank until added to the portfolio.
    watchlist_results = process_all_watchlists(
        sh, nc_cache=nc_cache,
        shared_prices=prices, shared_fund=fund_map,
        shared_tech=tech_map, shared_rev=rev_map
    )

    # ── Watchlist Opportunity Digest ───────────────────────────────────────────────
    # Identify watchlist stocks that have crossed into an attractive entry
    # window (score >= 50) and are NOT already held in the portfolio.
    # Uses data already computed above — no new API calls.
    portfolio_syms = set(holdings.keys())
    watchlist_opportunities = []
    C = GITHUB_DATA_COLS
    for tab_rows in watchlist_results.values():
        for row in tab_rows:
            try:
                sym    = row[C["symbol"]]
                tot_sc = row[C["total"]]
                action = row[C["action"]]
                rsi    = row[C["rsi"]]
                trend  = row[C["trend"]]
                setup  = row[C["technical_setup"]]
                news_s = row[C["news_sentiment"]]
            except (IndexError, KeyError):
                continue
            if sym in portfolio_syms:
                continue
            try:
                score_f = float(tot_sc)
            except (TypeError, ValueError):
                continue
            if score_f >= 50:
                watchlist_opportunities.append({
                    "sym": sym, "score": int(score_f), "action": action,
                    "rsi": rsi, "trend": trend, "setup": setup, "news": news_s,
                })
    watchlist_opportunities.sort(key=lambda x: x["score"], reverse=True)

    changes = history_tracker.compute_todays_changes(sh, results)
    prev_health_date, prev_health_score = history_tracker.get_previous_health_score(sh)

    dash = portfolio_analytics.compute_portfolio_dashboard(holdings, fund_map, trades, portfolio_live_value)
    health = portfolio_analytics.compute_portfolio_health(results, holdings, fund_map, dash)
    health_trend = portfolio_analytics.compute_health_trend(health["overall"], prev_health_score)

    history_tracker.append_history_snapshot(sh, results, portfolio_live_value, prices, health_score=health["overall"])

    portfolio_analytics.write_dashboard_tab(sh, dash, changes, health, health_trend)

    return {
        "results": results, "alerts": alerts,
        "portfolio_live_value": portfolio_live_value,
        "top_picks": top_picks, "failed": failed,
        "changes": changes,
        "watchlist_opportunities": watchlist_opportunities,
    }

def main():
    log.info("═" * 55)
    log.info("SIDDEGOWDA PORTFOLIO — Daily Auto-Update v2.0")
    log.info(f"Run time: {datetime.now().strftime('%d-%b-%Y %H:%M:%S')}")
    log.info("═" * 55)

    gc = get_gspread_client()
    sh = gc.open_by_key(SHEET_ID)
    log.info("Connected to Google Sheets")

    try:
        out = run_portfolio_update(sh)
    except Exception as e:
        log.error(f"run_portfolio_update FAILED: {e}", exc_info=True)
        send_telegram(f"❌ Portfolio update FAILED — {type(e).__name__}: {e}")
        raise
    if out is None:
        send_telegram("❌ Portfolio update FAILED — no symbols found in Portfolio tab col B")
        return

    msg = build_alert_message(out["alerts"], out["portfolio_live_value"], out["top_picks"],
                               watchlist_opps=out.get("watchlist_opportunities"))
    digest = history_tracker.format_telegram_digest(out.get("changes"))
    if digest:
        msg = msg + "\n\n" + digest
    if len(msg) > 4000:
        msg = msg[:4000] + "\n\n<i>...truncated</i>"
    send_telegram(msg)

    # ── Mutual Fund update (isolated — never breaks stock pipeline) ───
    try:
        mutual_fund_builder.run_mutual_fund_update(sh)
    except Exception as mf_e:
        log.error(f"Mutual Fund update FAILED: {mf_e}", exc_info=True)
        send_telegram(f"⚠️ Mutual Fund update failed — {type(mf_e).__name__}: {mf_e}")

    log.info("═" * 55)
    log.info(f"✅ {len(out['results'])} stocks updated | ❌ Failed: {out['failed'] or 'None'}")
    log.info(f"💰 Portfolio: ₹{out['portfolio_live_value']:,.0f}")
    log.info(f"🔴 SL Breach: {[a['sym'] for a in out['alerts']['sl_breach']] or 'None'}")
    log.info(f"🎯 Target Hit: {[a['sym'] for a in out['alerts']['target_hit']] or 'None'}")
    log.info(f"✅ Strong Buy: {[a['sym'] for a in out['alerts']['strong_buy'][:5]]}")
    log.info("Top 5 picks:")
    for r in out["top_picks"][:5]:
        log.info(f"   {r['sym']:<12} Score:{r['total']:>3}  {r['action']}")
    log.info("═" * 55)

if __name__ == "__main__":
    main()
