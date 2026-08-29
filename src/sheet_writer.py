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

class FormattingWriteError(Exception):
    """Raised by batch_update_safe when a batchUpdate slice still fails
    after both the normal retry loop AND chunk-size reduction have been
    exhausted. Callers should treat this as actionable — it means some
    formatting requests could not be applied and the target sheet may be
    left in a partially-formatted state — never catch-and-ignore it."""
    pass


def _send_slice_with_retry(sh, slice_, min_chunk=1):
    """Send one slice of batchUpdate requests with 429/5xx retry
    (existing behaviour). If the slice still fails after retries and it
    has more than `min_chunk` requests, split it in half (preserving
    original request order) and retry each half independently — this
    narrows down a transient failure instead of losing the whole chunk,
    and gives the requests that *do* succeed a chance to actually apply.
    If a single-request slice still cannot be applied, raises
    FormattingWriteError with full context instead of letting a bare/
    ambiguous exception propagate."""
    import gspread.exceptions
    RETRYABLE = ("429", "500", "502", "503", "504")
    last_err = None
    for attempt in range(5):  # up to 5 retries, same backoff as before
        try:
            profiler.increment("Sheets requests")
            sh.batch_update({"requests": slice_})
            time.sleep(1.5)   # 1.5 s between every chunk to stay under quota
            return
        except gspread.exceptions.APIError as e:
            last_err = e
            msg = str(e)
            code = next((c for c in RETRYABLE if c in msg), None)
            if code and attempt < 4:
                wait = 15 * (2 ** attempt)   # 15 s, 30 s, 60 s, 120 s, 240 s
                print(f"[retry] {code} hit on batch of {len(slice_)} requests, waiting {wait}s before retry {attempt+1}/5.")
                time.sleep(wait)
            else:
                break  # non-retryable error, or retries exhausted at this chunk size
        except Exception as e:
            last_err = e
            break  # unexpected error type — don't retry blindly, fall through to chunk-split/raise

    # Retries exhausted at this chunk size. If it can still be split,
    # halve it and retry each half in order — this is the "reduce
    # chunk size on retry" step, and it's what keeps a single bad/
    # oversized/rate-limited request from discarding an entire batch
    # of otherwise-valid formatting requests.
    if len(slice_) > min_chunk:
        mid = len(slice_) // 2
        _send_slice_with_retry(sh, slice_[:mid], min_chunk=min_chunk)
        _send_slice_with_retry(sh, slice_[mid:], min_chunk=min_chunk)
        return

    raise FormattingWriteError(
        f"batchUpdate request could not be applied after retries and "
        f"chunk-size reduction (down to {len(slice_)} request(s)): "
        f"{type(last_err).__name__}: {last_err}"
    ) from last_err


def batch_update_safe(sh, requests, chunk=30):
    """Send batchUpdate requests in chunks with retry on 429 quota errors
    and transient 500/502/503/504 Google-side outages. A chunk that still
    fails after retries is progressively split into smaller chunks (down
    to single requests) and retried before finally raising
    FormattingWriteError — this prevents a transient/partial Sheets API
    failure from silently discarding an entire batch of formatting
    (e.g. leaving a tab cleared but only partially re-styled)."""
    requests = preprocess_merge_requests(sh, requests)
    for i in range(0, len(requests), chunk):
        slice_ = requests[i:i + chunk]
        _send_slice_with_retry(sh, slice_)

def clear_sheet_safe(ws):
    """Safely clear a worksheet with retries for transient Google Sheets errors."""
    RETRYABLE = ("429", "500", "502", "503", "504")
    for attempt in range(5):
        try:
            ws.clear()
            break
        except Exception as e:
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
    RETRYABLE = ("429", "500", "502", "503", "504")
    for attempt in range(5):
        try:
            ws.update(*args, **kwargs)
            break
        except Exception as e:
            msg = str(e)
            code = next((c for c in RETRYABLE if c in msg), None)
            if code and attempt < 4:
                wait = 15 * (2 ** attempt)
                print(f"[retry] {code} on update_sheet, waiting {wait}s before retry {attempt+1}/5.")
                time.sleep(wait)
            else:
                raise
