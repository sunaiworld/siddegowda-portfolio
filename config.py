"""
Central config for the Telegram bot layer.
main.py keeps reading its own env vars directly (unchanged) —
this module is only imported by telegram_bot.py / handlers.py /
services/* so the bot side has one place to look.
"""
import os

BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")   # same var main.py already uses
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")  # same var main.py already uses
SHEET_ID  = os.environ.get("SHEET_ID", "")

BOT_NAME = "SiddeGowda Portfolio Bot"

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set")
if not SHEET_ID:
    raise ValueError("SHEET_ID not set")