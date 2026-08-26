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
from profiler import profiler

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


def preprocess_merge_requests(sh, requests):
    merge_reqs = [r["mergeCells"] for r in requests if "mergeCells" in r]
    if not merge_reqs:
        return requests
        
    sheet_ids = {m["range"].get("sheetId") for m in merge_reqs}
    sheet_ids = {sid for sid in sheet_ids if sid is not None}
    
    if not sheet_ids:
        return requests
        
    try:
        metadata = sh.fetch_sheet_metadata({"includeGridData": False})
        existing_merges = {}
        for sheet in metadata.get("sheets", []):
            props = sheet.get("properties", {})
            sid = props.get("sheetId")
            if sid in sheet_ids:
                existing_merges[sid] = sheet.get("merges", [])
                
        new_requests = []
        unmerged_existing = set() 
        
        for req in requests:
            if "mergeCells" in req:
                m_range = req["mergeCells"]["range"]
                sid = m_range.get("sheetId")
                if sid not in existing_merges:
                    new_requests.append(req)
                    continue
                
                r_start_r = m_range.get("startRowIndex", 0)
                r_end_r = m_range.get("endRowIndex", 1000000000)
                r_start_c = m_range.get("startColumnIndex", 0)
                r_end_c = m_range.get("endColumnIndex", 1000000000)
                
                exact_match = False
                unmerge_reqs = []
                
                for em in existing_merges[sid]:
                    e_start_r = em.get("startRowIndex", 0)
                    e_end_r = em.get("endRowIndex", 1000000000)
                    e_start_c = em.get("startColumnIndex", 0)
                    e_end_c = em.get("endColumnIndex", 1000000000)
                    
                    overlap_r = max(r_start_r, e_start_r) < min(r_end_r, e_end_r)
                    overlap_c = max(r_start_c, e_start_c) < min(r_end_c, e_end_c)
                    
                    if overlap_r and overlap_c:
                        if r_start_r == e_start_r and r_end_r == e_end_r and r_start_c == e_start_c and r_end_c == e_end_c:
                            exact_match = True
                        else:
                            em_tuple = (sid, e_start_r, e_end_r, e_start_c, e_end_c)
                            if em_tuple not in unmerged_existing:
                                unmerged_existing.add(em_tuple)
                                unmerge_reqs.append({
                                    "unmergeCells": {
                                        "range": {
                                            "sheetId": sid,
                                            "startRowIndex": e_start_r,
                                            "endRowIndex": e_end_r,
                                            "startColumnIndex": e_start_c,
                                            "endColumnIndex": e_end_c
                                        }
                                    }
                                })
                
                if exact_match:
                    log.info(f"[INFO] Skipping identical existing merge for range {m_range}")
                else:
                    if unmerge_reqs:
                        log.info(f"[INFO] Conflicting merged range detected. Unmerging {len(unmerge_reqs)} conflicting ranges before merge: {m_range}")
                        new_requests.extend(unmerge_reqs)
                    log.info(f"[INFO] Applying merge: {m_range}")
                    new_requests.append(req)
            else:
                new_requests.append(req)
        
        return new_requests
    except Exception as e:
        log.warning(f"Failed to preprocess merges: {e}")
        return requests

def batch_update_safe(sh, requests, chunk=30):
    requests = preprocess_merge_requests(sh, requests)
    """Send batchUpdate requests in small chunks with retry on 429 quota
    errors and transient 500/502/503/504 Google-side outages."""
    import gspread.exceptions
    RETRYABLE = ("429", "500", "502", "503", "504")
    for i in range(0, len(requests), chunk):
        slice_ = requests[i:i + chunk]
        for attempt in range(5):  # up to 5 retries
            try:
                profiler.increment("Sheets requests")
                sh.batch_update({"requests": slice_})
                time.sleep(1.5)   # 1.5 s between every chunk to stay under quota
                break
            except gspread.exceptions.APIError as e:
                msg = str(e)
                code = next((c for c in RETRYABLE if c in msg), None)
                if code and attempt < 4:
                    wait = 15 * (2 ** attempt)   # 15 s, 30 s, 60 s, 120 s, 240 s
                    print(f"[retry] {code} hit, waiting {wait}s before retry {attempt+1}/5.")
                    time.sleep(wait)
                else:
                    raise

def clear_sheet_safe(ws):
    """Safely clear a worksheet with retries for transient Google Sheets errors."""
    import gspread.exceptions
    RETRYABLE = ("429", "500", "502", "503", "504")
    for attempt in range(5):
        try:
            ws.clear()
            break
        except gspread.exceptions.APIError as e:
            msg = str(e)
            code = next((c for c in RETRYABLE if c in msg), None)
            if code and attempt < 4:
                wait = 15 * (2 ** attempt)
                print(f"[retry] {code} on clear_sheet, waiting {wait}s before retry {attempt+1}/5.")
                time.sleep(wait)
            else:
                raise

def update_sheet_safe(ws, *args, **kwargs):
    """Safely update a worksheet with retries for transient Google Sheets errors."""
    import gspread.exceptions
    RETRYABLE = ("429", "500", "502", "503", "504")
    for attempt in range(5):
        try:
            ws.update(*args, **kwargs)
            break
        except gspread.exceptions.APIError as e:
            msg = str(e)
            code = next((c for c in RETRYABLE if c in msg), None)
            if code and attempt < 4:
                wait = 15 * (2 ** attempt)
                print(f"[retry] {code} on update_sheet, waiting {wait}s before retry {attempt+1}/5.")
                time.sleep(wait)
            else:
                raise
