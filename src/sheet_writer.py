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
from score_engine import *
from sheet_writer import *
from sheet_formatter import *
from github_data_builder import *
from growth_screener_builder import *
from future_buy_builder import *
from data_fetcher import *
from telegram_alerts import *
from portfolio_builder import *



# ══════════════════════════════════════════════
# GOOGLE SHEETS AUTH
# ══════════════════════════════════════════════
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_gspread_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise ValueError("GOOGLE_CREDENTIALS_JSON not set.")
    creds = Credentials.from_service_account_info(
        json.loads(creds_json), scopes=SCOPES
    )
    return gspread.authorize(creds)



def batch_update_safe(sh, requests, chunk=30):
    """Send batchUpdate requests in small chunks with retry on 429 quota errors."""
    import gspread.exceptions
    for i in range(0, len(requests), chunk):
        slice_ = requests[i:i + chunk]
        for attempt in range(5):  # up to 5 retries
            try:
                sh.batch_update({"requests": slice_})
                time.sleep(1.5)   # 1.5 s between every chunk to stay under quota
                break
            except gspread.exceptions.APIError as e:
                if "429" in str(e):
                    wait = 15 * (2 ** attempt)   # 15 s, 30 s, 60 s, 120 s, 240 s
                    print(f"[quota] 429 hit, waiting {wait}s before retry {attempt+1}/5…")
                    time.sleep(wait)
                else:
                    raise

