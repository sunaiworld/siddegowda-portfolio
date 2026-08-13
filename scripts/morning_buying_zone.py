#!/usr/bin/env python3
"""
SIDDEGOWDA PORTFOLIO - Morning Buying Zone Telegram Update
Runs at 10:00 AM IST via GitHub Actions.
Reads the GITHUB DATA sheet and sends a summary of Buying Zone opportunities.
"""

import sys
import os
import logging
from datetime import datetime, timezone, timedelta
import yfinance as yf
import traceback

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from sheet_writer import get_gspread_client
from telegram_alerts import send_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("morning_update")

def is_market_open():
    """
    Checks if today is a trading day by looking at NIFTY 50's latest data timestamp.
    Returns (is_open, current_val, change_pct)
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    
    # 1. Weekend check
    if now_ist.weekday() >= 5:  # 5=Sat, 6=Sun
        log.info("Weekend detected. Market closed.")
        return False, None, None
        
    try:
        nifty = yf.Ticker("^NSEI")
        df = nifty.history(period="5d")
        if df.empty:
            log.warning("No data for NIFTY 50.")
            return False, None, None
            
        last_date = df.index[-1].date()
        if last_date != now_ist.date():
            log.info(f"Market holiday detected. Latest NSE date is {last_date}, today is {now_ist.date()}")
            return False, None, None
            
        current_val = df['Close'].iloc[-1]
        prev_val = df['Close'].iloc[-2]
        change_pct = ((current_val - prev_val) / prev_val) * 100
        
        return True, current_val, change_pct
    except Exception as e:
        log.error(f"Error checking market open status: {e}")
        return False, None, None

def generate_morning_message():
    # 1. Market Open Check
    is_open, nifty_val, nifty_pct = is_market_open()
    if not is_open:
        log.info("Market is not open or it's a holiday. Aborting Telegram update.")
        return
        
    log.info("Market is open. Fetching GITHUB DATA...")
    
    # 2. Fetch GITHUB DATA
    try:
        client = get_gspread_client()
        sh = client.open("siddegowda-portfolio")
        ws = sh.worksheet("GITHUB DATA")
        records = ws.get_all_records()
    except Exception as e:
        log.error(f"Failed to fetch GITHUB DATA sheet: {e}")
        return
        
    if not records:
        log.warning("GITHUB DATA sheet is empty.")
        return
        
    # 3. Categorize opportunities
    categories = {
        "🟢🟢 ADD AGGRESSIVELY": [],
        "🔎 INVESTIGATE WHY": [],
        "🟢 ACCUMULATE": [],
        "🟡 SMALL BUY": [],
        "❌ WAIT": []
    }
    
    for row in records:
        zone = row.get("Buying Zone", "").strip()
        if zone in categories:
            categories[zone].append(row)
            
    # 4. Build Telegram Message
    ist = timezone(timedelta(hours=5, minutes=30))
    now_str = datetime.now(ist).strftime("%d-%b-%Y | %I:%M %p IST")
    sign = "+" if nifty_pct >= 0 else ""
    
    msg = f"📊 <b>MORNING BUYING ZONE</b>\n"
    msg += f"{now_str}\n\n"
    msg += f"<b>NIFTY 50:</b> {nifty_val:,.2f} ({sign}{nifty_pct:.2f}%)\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += "<b>🟢 BUYING ZONE GUIDE</b>\n\n"
    msg += "<b>❌ WAIT</b>\nExpensive → Wait\n\n"
    msg += "<b>🟡 SMALL BUY</b>\nReasonable → Small entry\n\n"
    msg += "<b>🟢 ACCUMULATE</b>\nAttractive → Build gradually\n\n"
    msg += "<b>🟢🟢 ADD AGGRESSIVELY</b>\nVery attractive + strong fundamentals → Add more\n\n"
    msg += "<b>🔎 INVESTIGATE WHY</b>\nExceptionally cheap → Find out why first\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += "<b>FORTIS EXAMPLE ONLY</b>\n\n"
    msg += "&gt; ₹1,050     ❌ Wait\n"
    msg += "₹950–1,050   🟡 Small Buy\n"
    msg += "₹850–950     🟢 Accumulate\n"
    msg += "₹750–850     🟢🟢 Add Aggressively\n"
    msg += "&lt; ₹750       🔎 Investigate Why\n\n"
    msg += "<i>Example only. These prices are NOT universal thresholds.</i>\n\n"
    
    msg += "━━━━━━━━━━━━━━━━━━\n\n"
    msg += "<b>🔥 TODAY'S OPPORTUNITIES</b>\n\n"
    
    # Priority rendering order
    render_order = [
        "🟢🟢 ADD AGGRESSIVELY",
        "🔎 INVESTIGATE WHY",
        "🟢 ACCUMULATE",
        "🟡 SMALL BUY"
    ]
    
    for cat in render_order:
        msg += f"<b>{cat}</b>\n"
        stocks = categories[cat]
        if not stocks:
            msg += "None today.\n\n"
        else:
            for s in stocks:
                sym = str(s.get("Symbol", "?")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                cmp = s.get("CMP", 0)
                pe = s.get("PE", "-")
                score = s.get("Total Score", "-")
                action = str(s.get("Final Action", "-")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                price_rng = str(s.get("Buy/Sell Price Range", "")).replace("&", "&amp;")
                price_rng_line = f"Buy Zone: {price_rng}\n" if price_rng else ""
                msg += f"<b>{sym}</b>\nCMP: ₹{cmp}\n{price_rng_line}PE: {pe} | Score: {score}\nFinal Action: {action}\n\n"
            msg += "\n"
            
    # Render WAIT differently
    wait_count = len(categories["❌ WAIT"])
    msg += "<b>❌ WAIT</b>\n"
    if wait_count < 5:
        stocks = categories["❌ WAIT"]
        if not stocks:
             msg += "None today.\n"
        else:
             for s in stocks:
                sym = str(s.get("Symbol", "?")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                cmp = s.get("CMP", 0)
                action = str(s.get("Final Action", "-")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                msg += f"• {sym} | ₹{cmp} | {action}\n"
    else:
        msg += f"{wait_count} stocks\n"
        
    msg += "\n━━━━━━━━━━━━━━━━━━\n\n"
    msg += "⚠️ <i>This is a screening/decision-support signal, not a guaranteed buy recommendation.</i>"
    
    log.info("Sending Telegram message...")
    success = send_telegram(msg)
    if success:
        log.info("Morning buying zone update sent successfully!")
    else:
        log.error("Failed to send telegram update.")

if __name__ == "__main__":
    generate_morning_message()
