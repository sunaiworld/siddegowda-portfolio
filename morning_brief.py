#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO - Morning Brief
Runs at 08:00 AM IST, every day, via GitHub Actions.

1. Skips if today's brief was already delivered (Bot State!B2).
2. Builds the brief: price alerts, rule checks, news (src/morning_brief_builder.py).
3. Sends it to Telegram.
4. Records today's date in Bot State!B2 ONLY after Telegram succeeded,
   so a backup cron retries after a failure and skips after a success.

Bot State!B1 belongs to the 10 AM Morning Buying Zone job and is never touched here.

Flags:
  --dry-run  build the brief and print it; no Telegram, no sheet write
  --force    ignore the once-per-day marker and the 11:00 IST cutoff
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from config import SHEET_ID
from sheet_writer import get_gspread_client
from telegram_alerts import send_telegram
import morning_brief_builder as builder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("morning_brief")

IST = timezone(timedelta(hours=5, minutes=30))
STATE_TAB = "Bot State"
STATE_CELL = "B2"
CUTOFF_HOUR_IST = 11   # last backup cron fires 09:30 IST; anything past 11:00 is stale


def get_last_brief_date(sh):
    try:
        val = sh.worksheet(STATE_TAB).acell(STATE_CELL).value
        return str(val).strip() if val else ""
    except Exception as e:
        log.warning(f"[brief] could not read {STATE_TAB}!{STATE_CELL}: {e}")
        return ""


def set_last_brief_date(sh, date_str, retries=3):
    for attempt in range(retries):
        try:
            try:
                ws = sh.worksheet(STATE_TAB)
            except Exception:
                ws = sh.add_worksheet(STATE_TAB, rows=2, cols=4)
            ws.update_acell(STATE_CELL, date_str)
            log.info(f"[brief] {STATE_TAB}!{STATE_CELL} set to {date_str}")
            return True
        except Exception as e:
            wait = (attempt + 1) * 5
            log.warning(f"[brief] state write {attempt + 1}/{retries} failed: {e}. Retrying in {wait}s")
            time.sleep(wait)
    return False


def main():
    parser = argparse.ArgumentParser(description="Morning Brief")
    parser.add_argument("--dry-run", action="store_true", help="Build and print only")
    parser.add_argument("--force", action="store_true", help="Bypass once-per-day marker and cutoff")
    args = parser.parse_args()

    now_ist = datetime.now(IST)
    today = now_ist.strftime("%Y-%m-%d")
    log.info(f"[brief] start {now_ist.strftime('%Y-%m-%d %H:%M:%S IST')}")

    try:
        client = get_gspread_client()
        sh = client.open_by_key(SHEET_ID) if SHEET_ID else client.open("siddegowda-portfolio")
    except Exception as e:
        log.error(f"[brief] could not open the sheet: {e}")
        sys.exit(1)

    if not args.dry_run and not args.force:
        if now_ist.hour >= CUTOFF_HOUR_IST:
            log.error(f"[brief] {now_ist.strftime('%I:%M %p IST')} is past the {CUTOFF_HOUR_IST}:00 cutoff "
                      f"(schedule delay). Not sending a stale brief. Use --force to override.")
            sys.exit(1)
        if get_last_brief_date(sh) == today:
            log.info(f"[brief] {today} already delivered. Skipping.")
            return

    try:
        message = builder.build_brief(sh, now_ist)
    except Exception:
        log.exception("[brief] failed to build the brief")
        sys.exit(1)

    if args.dry_run:
        log.info("[brief] DRY RUN - message below, nothing sent, nothing written")
        print("\n" + message + "\n")
        return

    if not send_telegram(message):
        log.error("[brief] Telegram send failed. Marker NOT set, so a backup run can retry.")
        sys.exit(1)

    if not set_last_brief_date(sh, today):
        log.error("[brief] Brief was sent but the marker could not be saved; "
                  "a backup run may send a duplicate.")
        sys.exit(1)
    log.info("[brief] done")


if __name__ == "__main__":
    main()
