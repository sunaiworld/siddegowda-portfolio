"""
mf_history_fetcher.py
Fetches historical Mutual Fund NAVs from the mfapi.in API.
"""

import urllib.request
import json
import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", ".cache", "mf_history")
os.makedirs(CACHE_DIR, exist_ok=True)

def fetch_historical_nav(scheme_code):
    """
    Fetches historical NAV for a given scheme code from mfapi.in.
    Returns a list of dicts: [{"date": "dd-mm-yyyy", "nav": "float"}, ...]
    """
    if not scheme_code:
        return []

    cache_file = os.path.join(CACHE_DIR, f"{scheme_code}.json")
    
    # Check cache (valid for 1 day)
    if os.path.exists(cache_file):
        mtime = os.path.getmtime(cache_file)
        if (datetime.now().timestamp() - mtime) < 86400:
            try:
                with open(cache_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass

    url = f"https://api.mfapi.in/mf/{scheme_code}"
    log.info(f"Fetching historical NAV for scheme code {scheme_code}...")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get("status") == "SUCCESS" and "data" in data:
                nav_data = data["data"]
                # Save to cache
                try:
                    with open(cache_file, "w") as f:
                        json.dump(nav_data, f)
                except Exception as e:
                    log.warning(f"Could not write cache for {scheme_code}: {e}")
                return nav_data
    except Exception as e:
        log.error(f"Failed to fetch historical data for {scheme_code}: {e}")
    
    return []

def get_nav_on_date(historical_data, target_date_str, format="%d-%m-%Y"):
    """
    Finds the NAV on or just before the target date.
    target_date_str is expected in format '%d-%m-%Y'.
    historical_data is a list of {"date": "dd-mm-yyyy", "nav": "12.34"} ordered newest to oldest.
    """
    if not historical_data:
        return None
    
    try:
        target_date = datetime.strptime(target_date_str, format).date()
    except ValueError:
        return None

    # mfapi data is typically ordered descending by date
    for entry in historical_data:
        try:
            entry_date = datetime.strptime(entry["date"], "%d-%m-%Y").date()
            if entry_date <= target_date:
                return float(entry["nav"])
        except (ValueError, KeyError, TypeError):
            continue
            
    return None
