#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO — Morning Buying Zone Update
Runs at 10:00 AM IST via GitHub Actions.

1. Checks whether today is an NSE trading day.
2. Fetches today's 10:00 AM IST market data & technical indicators for stocks in GITHUB DATA.
3. Recalculates scoring, Quality/Valuation/Timing/Total scores, Buying Zones, Price Ranges, and Final Actions.
4. Writes updated values back to GITHUB DATA worksheet in Google Sheets (preserving layout & formatting).
5. Reads updated GITHUB DATA sheet and sends Telegram update message.
6. Supports --dry-run CLI mode for testing without modifying Google Sheets or Telegram.
"""

import sys
import os
import argparse
import logging
import html
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import yfinance as yf

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from config import SHEET_ID, TECH_WORKERS
from sheet_writer import get_gspread_client
from telegram_alerts import send_telegram
from github_data_builder import GITHUB_DATA_COLS, GITHUB_DATA_HEADER_NAMES, build_result_row, write_github_data
from data_fetcher import fetch_prices_batch, fetch_technicals, fetch_rev_growth
import fund_cache
import news_engine.news_cache as news_cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("morning_update")

def html_escape(val):
    if val is None:
        return ""
    return html.escape(str(val))

def is_market_open():
    """
    Checks if today is a trading day by looking at weekday and NIFTY 50 data.
    Returns (is_open, current_val, change_pct)
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)

    # 1. Weekend check
    if now_ist.weekday() >= 5:  # 5=Sat, 6=Sun
        log.info("[Morning] Weekend detected. Market closed.")
        return False, None, None

    # 2. Check NIFTY 50 data via yfinance
    try:
        nifty = yf.Ticker("^NSEI")
        df = nifty.history(period="5d")
        if df.empty:
            df = nifty.history(period="1d", interval="5m")

        if not df.empty:
            current_val = float(df['Close'].iloc[-1])
            prev_val = float(df['Close'].iloc[-2]) if len(df) >= 2 else current_val
            change_pct = ((current_val - prev_val) / prev_val) * 100 if prev_val else 0.0

            last_date = df.index[-1].date()
            if last_date == now_ist.date():
                log.info(f"[Morning] Confirmed trading day with today's NSE data ({last_date}).")
                return True, current_val, change_pct
            else:
                # If during market hours (09:15 - 15:30 IST) on a weekday, proceed even if Yahoo daily candle is slightly delayed
                market_start = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
                market_end = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
                if market_start <= now_ist <= market_end:
                    log.info(f"[Morning] Market hours detected on weekday. Using latest NIFTY 50 value {current_val:.2f}.")
                    return True, current_val, change_pct
                else:
                    log.info(f"[Morning] Latest NSE date is {last_date}, today is {now_ist.date()}. Outside active trading.")
                    return True, current_val, change_pct
        else:
            log.warning("[Morning] No data for NIFTY 50, proceeding on weekday.")
            return True, 0.0, 0.0
    except Exception as e:
        log.error(f"[Morning] Error checking market open status: {e}. Proceeding on weekday.")
        return True, 0.0, 0.0

def send_telegram_morning_update(records, nifty_val, nifty_pct):
    categories = {
        "🟢🟢 ADD AGGRESSIVELY": [],
        "🔎 INVESTIGATE WHY": [],
        "🟢 ACCUMULATE": [],
        "🟡 SMALL BUY": [],
        "❌ WAIT": []
    }

    for row in records:
        zone = str(row.get("Buying Zone", "")).strip()
        if zone in categories:
            categories[zone].append(row)

    # Sort opportunities within each category by Total Score descending
    for cat in categories:
        categories[cat].sort(
            key=lambda x: float(x.get("Total Score", 0)) if str(x.get("Total Score", "")).replace(".", "").isdigit() else 0,
            reverse=True
        )

    ist = timezone(timedelta(hours=5, minutes=30))
    now_str = datetime.now(ist).strftime("%d-%b-%Y | %I:%M %p IST")

    nifty_v_str = f"{nifty_val:,.2f}" if isinstance(nifty_val, (int, float)) and nifty_val > 0 else "N/A"
    sign = "+" if (isinstance(nifty_pct, (int, float)) and nifty_pct >= 0) else ""
    nifty_p_str = f"({sign}{nifty_pct:.2f}%)" if isinstance(nifty_pct, (int, float)) else ""

    msg = f"📊 <b>MORNING BUYING ZONE UPDATE</b>\n"
    msg += f"<i>{now_str}</i>\n"
    msg += f"<b>NIFTY 50:</b> {nifty_v_str} {nifty_p_str}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Display Top Opportunities
    render_categories = [
        ("🟢🟢 ADD AGGRESSIVELY", categories["🟢🟢 ADD AGGRESSIVELY"], 8),
        ("🔎 INVESTIGATE WHY", categories["🔎 INVESTIGATE WHY"], 6),
        ("🟢 ACCUMULATE", categories["🟢 ACCUMULATE"], 6),
        ("🟡 SMALL BUY", categories["🟡 SMALL BUY"], 6),
    ]

    total_opps = sum(len(stocks) for _, stocks, _ in render_categories)
    msg += f"<b>🔥 TODAY'S OPPORTUNITIES ({total_opps} stocks)</b>\n\n"

    for cat_name, stocks, max_show in render_categories:
        if not stocks:
            continue
        msg += f"<b>{cat_name} ({len(stocks)})</b>\n"
        for s in stocks[:max_show]:
            sym = html_escape(s.get("Symbol", "?"))
            cmp = html_escape(s.get("CMP", 0))
            score = html_escape(s.get("Total Score", "-"))
            action = html_escape(s.get("Final Action", "-"))
            price_rng = html_escape(s.get("Buy/Sell Price Range", ""))
            rng_str = f" | Zone: {price_rng}" if price_rng else ""
            msg += f"  • <b>{sym}</b>: CMP ₹{cmp} (Score {score}){rng_str} → {action}\n"
        if len(stocks) > max_show:
            msg += f"  <i>...and {len(stocks) - max_show} more</i>\n"
        msg += "\n"

    # Wait summary
    wait_count = len(categories["❌ WAIT"])
    msg += f"<b>❌ WAIT / EXPENSIVE:</b> {wait_count} stocks\n"
    msg += "\n━━━━━━━━━━━━━━━━━━━━\n"
    msg += "⚠️ <i>Screening and decision-support signals. Review portfolio fit before entering.</i>"

    log.info(f"[Morning] Sending Telegram update ({len(msg)} characters)")
    success = send_telegram(msg)
    if success:
        log.info("[Morning] Telegram update sent successfully!")
    else:
        log.error("[Morning] Failed to send telegram update.")
    return success

def main():
    parser = argparse.ArgumentParser(description="Morning Buying Zone Update")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry-run without modifying Google Sheets or sending Telegram")
    args = parser.parse_args()

    log.info("[Morning] Starting market update")

    # 1. Check market open status
    is_open, nifty_val, nifty_pct = is_market_open()
    if not is_open:
        if not args.dry_run:
            log.info("[Morning] Market is not open or it's a holiday. Aborting update.")
            return
        else:
            log.info("[Morning] Market check: Closed/Holiday, but proceeding because --dry-run is enabled.")
            if nifty_val is None:
                nifty_val, nifty_pct = 0.0, 0.0
    else:
        log.info("[Morning] Trading day confirmed")

    log.info("[Morning] Connecting to Google Sheets...")
    try:
        client = get_gspread_client()
        sh = client.open_by_key(SHEET_ID) if SHEET_ID else client.open("siddegowda-portfolio")
        ws = sh.worksheet("GITHUB DATA")
        all_vals = ws.get_all_values()
    except Exception as e:
        log.error(f"[Morning] Failed to connect or read GITHUB DATA sheet: {e}")
        return

    if len(all_vals) <= 2:
        log.warning("[Morning] GITHUB DATA sheet is empty or missing data rows.")
        return

    # Extract symbols and baseline rows from GITHUB DATA
    data_rows = all_vals[2:]
    symbols = []
    prev_row_map = {}
    for r in data_rows:
        if not r or not r[0].strip():
            continue
        sym = r[0].strip().upper()
        symbols.append(sym)
        prev_row_map[sym] = r

    total_symbols = len(symbols)
    log.info(f"[Morning] Found {total_symbols} stock(s) in GITHUB DATA")
    log.info("[Morning] Fetching market data")

    # ---------------------------------------------------------------------------
    # CMP DATA SOURCE NOTE:
    # Daily 1d Yahoo data (via fetch_prices_batch / fetch_technicals) is used
    # for full compatibility with the existing portfolio pipeline architecture.
    # Yahoo Finance market data may be delayed (typically ~15 minutes).
    # The CMP value represents the latest available Yahoo market price at
    # workflow execution time (approx 10:00 AM IST), not guaranteed real-time NSE tick data.
    # ---------------------------------------------------------------------------

    # Batch price download
    prices = fetch_prices_batch(symbols)

    # Load cached fundamentals and news
    try:
        fc_cache = fund_cache.load_cache(sh)
    except Exception as e:
        log.warning(f"[Morning] Could not load fund_cache: {e}")
        fc_cache = {}

    try:
        nc_cache, _, _ = news_cache.load(sh)
    except Exception as e:
        log.warning(f"[Morning] Could not load news_cache: {e}")
        nc_cache = {}

    # Parallel technicals & growth fetch
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
                log.warning(f"[Morning] Failed tech/growth for {sym}: {e}")
                tech, rev_gr = {}, None
            tech_map[sym] = tech
            rev_map[sym] = rev_gr

    log.info("[Morning] Recalculating Buying Zones")

    updated_rows = []
    changes = []
    processed_count = 0
    C = GITHUB_DATA_COLS

    for sym in symbols:
        processed_count += 1
        cmp = prices.get(sym)
        prev_raw = prev_row_map.get(sym, [""] * len(C))

        if cmp is None or not (isinstance(cmp, (int, float)) and cmp > 0):
            log.warning(f"[Morning] Stock {sym} market price unavailable. Preserving previous values.")
            updated_rows.append(prev_raw)
            continue

        f = fund_cache.get_or_fetch_fundamentals(sym, fc_cache, max_age_days=7)
        tech = tech_map.get(sym, {})
        rev_gr = rev_map.get(sym)
        nd = nc_cache.get(sym.upper(), {})

        try:
            new_row, archetype, tot_sc, final_action = build_result_row(
                sym, cmp, f, tech, rev_gr, xirr_val="", news_data=nd
            )

            # Preserve news fields if build_result_row left them blank but prev_raw had content
            for news_col in ("news_summary", "news_sentiment", "news_source"):
                idx = C[news_col]
                if idx < len(new_row) and not new_row[idx] and idx < len(prev_raw) and prev_raw[idx]:
                    new_row[idx] = prev_raw[idx]

            updated_rows.append(new_row)

            prev_cmp = prev_raw[C["cmp"]] if C["cmp"] < len(prev_raw) else ""
            prev_zone = prev_raw[C["buying_zone"]] if C["buying_zone"] < len(prev_raw) else ""
            prev_score = prev_raw[C["total"]] if C["total"] < len(prev_raw) else ""
            prev_act = prev_raw[C["action"]] if C["action"] < len(prev_raw) else ""

            new_zone = new_row[C["buying_zone"]]
            new_score = new_row[C["total"]]
            new_act = new_row[C["action"]]

            if str(prev_zone) != str(new_zone) or str(prev_cmp) != str(cmp) or str(prev_score) != str(new_score):
                changes.append({
                    "symbol": sym,
                    "old_cmp": prev_cmp, "new_cmp": cmp,
                    "old_zone": prev_zone, "new_zone": new_zone,
                    "old_score": prev_score, "new_score": new_score,
                    "old_action": prev_act, "new_action": new_act,
                })
        except Exception as e:
            log.error(f"[Morning] Error recalculating {sym}: {e}. Preserving previous values.")
            updated_rows.append(prev_raw)

    log.info(f"[Morning] Stocks processed: {processed_count}/{total_symbols}")

    if args.dry_run:
        log.info("[Morning] DRY-RUN MODE RESULTS:")
        log.info(f"[Morning] Total stocks evaluated: {len(updated_rows)}")
        log.info(f"[Morning] Total stocks with changes: {len(changes)}")
        for ch in changes:
            log.info(
                f"[Morning] CHANGE | {ch['symbol']:<10} | CMP: {ch['old_cmp']} -> {ch['new_cmp']} | "
                f"Zone: '{ch['old_zone']}' -> '{ch['new_zone']}' | Score: {ch['old_score']} -> {ch['new_score']}"
            )
        log.info("[Morning] DRY-RUN completed. No Google Sheet or Telegram modifications were made.")
        return

    # Write updated values back to Google Sheet
    log.info("[Morning] Updating GITHUB DATA")
    try:
        write_github_data(sh, updated_rows, tab_name="GITHUB DATA")
        log.info("[Morning] Google Sheet update successful")
    except Exception as e:
        log.error(f"[Morning] Failed to update Google Sheets: {e}")
        return

    # Build records array directly from updated_rows in memory
    log.info("[Morning] Reading updated GITHUB DATA")
    records = []
    headers = [GITHUB_DATA_HEADER_NAMES.get(k, k) for k in GITHUB_DATA_COLS.keys()]
    for r in updated_rows:
        records.append({headers[i]: r[i] for i in range(min(len(r), len(headers)))})

    # Send Telegram message
    success = send_telegram_morning_update(records, nifty_val, nifty_pct)
    if not success:
        log.error("[Morning] Telegram update delivery failed! Exiting with status code 1 so GitHub Actions flags failure.")
        sys.exit(1)

    log.info("[Morning] Completed successfully")

if __name__ == "__main__":
    main()
