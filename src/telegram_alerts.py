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

import portfolio_analytics

from news_engine.sources import google_news_rss
from news_engine import classifier

log = logging.getLogger("portfolio")

from config import *


# ══════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def chunk_message(text, max_len=4000):
    if len(text) <= max_len:
        return [text]
    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > max_len:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
            else:
                # Single line exceeds max_len, hard-split
                chunks.append(line[:max_len])
                current_chunk = [line[max_len:]]
                current_len = len(line[max_len:])
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks

def send_telegram(message):
    token = os.environ.get("TELEGRAM_TOKEN", "") or TELEGRAM_TOKEN
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "") or TELEGRAM_CHAT_ID
    if not token or not chat_id:
        log.warning("Telegram not configured — skipping alert")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        chunks = chunk_message(message, max_len=4000)
        all_success = True
        for idx, chunk in enumerate(chunks):
            data = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            resp = requests.post(url, data=data, timeout=10)
            if resp.status_code == 200:
                log.info(f"Telegram alert sent (part {idx + 1}/{len(chunks)})")
            else:
                log.warning(f"Telegram failed (part {idx + 1}/{len(chunks)}): {resp.text}")
                all_success = False
            if len(chunks) > 1 and idx < len(chunks) - 1:
                time.sleep(0.5)
        return all_success
    except Exception as e:
        log.warning(f"Telegram error: {e}")
        return False


def build_alert_message(alerts, portfolio_value, top_results, watchlist_opps=None, health_score=None, health_trend=None):
    now = datetime.now().strftime("%d-%b-%Y %H:%M")
    msg  = "<b>SiddeGowda Portfolio Update</b>\n"
    msg += f"<i>{now} IST</i>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"💰 <b>Portfolio Value</b>: ₹{portfolio_value:,.0f}\n"

    if health_score is not None:
        trend_str = f" | {health_trend[0]}" if health_trend and isinstance(health_trend, (list, tuple)) else ""
        delta_str = f" ({health_trend[1]:+0.1f})" if health_trend and len(health_trend) > 1 and health_trend[1] is not None else ""
        msg += f"📊 <b>Health Score</b>: {health_score}/100{trend_str}{delta_str}\n"

    msg += "\n"

    # 1. Urgent Action: Stop Loss Breaches (Capital-weighted)
    sl_list = list(alerts.get("sl_breach", []))
    if sl_list:
        sl_list.sort(key=lambda x: x.get("loss_amount", 0) or 0, reverse=True)
        msg += f"<b>🔴 URGENT ACTION: STOP-LOSS BREACHES ({len(sl_list)})</b>\n"
        for a in sl_list[:6]:
            sc_tag = " (SC)" if a.get("is_smallcase") else ""
            loss_str = f" | Loss -₹{a['loss_amount']:,.0f} ({a.get('return_pct', 0):+.1f}%)" if a.get("loss_amount") else ""
            msg += f"  • <b>{a['sym']}</b>{sc_tag}: CMP ₹{a['cmp']} (SL ₹{a['sl']}){loss_str}\n"
        if len(sl_list) > 6:
            msg += f"  <i>...and {len(sl_list) - 6} more</i>\n"
        msg += "\n"

    # 2. Profit Harvesting: Targets Hit (Profit-weighted)
    tgt_list = list(alerts.get("target_hit", []))
    if tgt_list:
        tgt_list.sort(key=lambda x: x.get("gain_amount", 0) or 0, reverse=True)
        msg += f"<b>🎯 PROFIT HARVESTING: TARGETS HIT ({len(tgt_list)})</b>\n"
        for a in tgt_list[:6]:
            sc_tag = " (SC)" if a.get("is_smallcase") else ""
            gain_str = f" | Gain +₹{a['gain_amount']:,.0f} ({a.get('return_pct', 0):+.1f}%)" if a.get("gain_amount") else ""
            msg += f"  • <b>{a['sym']}</b>{sc_tag}: CMP ₹{a['cmp']} (Tgt ₹{a['tgt']}){gain_str}\n"
        if len(tgt_list) > 6:
            msg += f"  <i>...and {len(tgt_list) - 6} more</i>\n"
        msg += "\n"

    # 3. Top 3 Fresh Opportunities
    if watchlist_opps:
        msg += "<b>💎 Top Fresh Buy Opportunities</b>\n"
        for idx, o in enumerate(watchlist_opps[:3], 1):
            rsi_str = f" | RSI {o['rsi']}" if o.get('rsi') != "" else ""
            fit_str = f" | {o['fit']}" if o.get('fit') else ""
            msg += f"  {idx}. <b>{o['sym']}</b> — {o['action']} (Score: {o['score']}{rsi_str}{fit_str})\n"
        msg += "\n"
    elif top_results:
        msg += "<b>🏆 Top 3 Picks Today</b>\n"
        for idx, r in enumerate(top_results[:3], 1):
            msg += f"  {idx}. <b>{r['sym']}</b> — {r['action']} (Score: {r['total']})\n"
        msg += "\n"

    # 4. Routine Signals Summary
    sb_count = len(alerts.get("strong_buy", []))
    sell_count = len(alerts.get("sell_watch", []))
    if sb_count or sell_count:
        msg += f"📋 <i>Signals Pulse: {sb_count} Buys | {sell_count} Sells/Avoids</i>\n"

    msg += "\n<i>via GitHub Actions + yfinance</i>"
    return msg
