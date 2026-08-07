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

log = logging.getLogger("portfolio")

from config import *


# ══════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured — skipping alert")
        return False
    try:
        url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        resp = requests.post(url, data=data, timeout=10)
        if resp.status_code == 200:
            log.info("Telegram alert sent")
            return True
        else:
            log.warning(f"Telegram failed: {resp.text}")
            return False
    except Exception as e:
        log.warning(f"Telegram error: {e}")
        return False


def build_alert_message(alerts, portfolio_value, top_results, watchlist_opps=None):
    now = datetime.now().strftime("%d-%b-%Y %H:%M")
    msg  = f"<b>SiddeGowda Portfolio Update</b>\n"
    msg += f"<i>{now} IST</i>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 Portfolio: ₹{portfolio_value:,.0f}\n\n"

    if alerts["sl_breach"]:
        msg += "<b>🔴 STOP LOSS BREACHED</b>\n"
        for a in alerts["sl_breach"][:5]:
            msg += f"  {a['sym']} — CMP ₹{a['cmp']} | SL ₹{a['sl']}\n"
        msg += "\n"

    if alerts["target_hit"]:
        msg += "<b>🎯 TARGET HIT</b>\n"
        for a in alerts["target_hit"][:5]:
            msg += f"  {a['sym']} — CMP ₹{a['cmp']} | Target ₹{a['tgt']}\n"
        msg += "\n"

    if alerts["strong_buy"]:
        msg += "<b>✅ STRONG BUY / BUY</b>\n"
        for a in alerts["strong_buy"][:5]:
            msg += f"  {a['sym']} — Score:{a['score']} | {a['action']}\n"
        msg += "\n"

    if alerts["sell_watch"]:
        msg += "<b>⚠️ AVOID / SELL</b>\n"
        for a in alerts["sell_watch"][:5]:
            msg += f"  {a['sym']} — Score:{a['score']} | {a['action']}\n"
        msg += "\n"

    if top_results:
        msg += "<b>🏆 Top 3 Picks Today</b>\n"
        for r in top_results[:3]:
            msg += f"  {r['sym']} — {r['action']} (Score:{r['total']})\n"

    if watchlist_opps:
        msg += "\n<b>📋 Watchlist Opportunities</b>\n"
        for o in watchlist_opps[:5]:
            rsi_str  = f" | RSI {o['rsi']}" if o['rsi'] != "" else ""
            news_str = f" | News:{o['news']}" if o['news'] else ""
            msg += f"  {o['sym']} — {o['action']} (Score:{o['score']}{rsi_str}{news_str})\n"

    msg += "\n<i>via GitHub Actions + yfinance</i>"
    return msg
