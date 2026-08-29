"""
mf_data_fetcher.py
Fetches daily Mutual Fund NAVs and ISIN mappings from the official AMFI API.
"""

import urllib.request
import logging

log = logging.getLogger(__name__)

AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

_cache_navs = None

def fetch_amfi_data():
    """
    Returns a dict mapping ISIN -> {
        "nav": float,
        "name": str,
        "scheme_code": str,
        "date": str
    }
    """
    global _cache_navs
    if _cache_navs is not None:
        return _cache_navs

    log.info("Fetching AMFI Mutual Fund NAV data...")
    try:
        req = urllib.request.Request(AMFI_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read().decode('utf-8')
    except Exception as e:
        log.error(f"Failed to fetch AMFI data: {e}")
        return {}

    parsed = {}
    for line in data.splitlines():
        line = line.strip()
        if not line or ";" not in line:
            continue
        parts = line.split(";")
        if len(parts) >= 5:
            code = parts[0]
            isin = parts[1].strip()
            name = parts[3].strip()
            try:
                nav = float(parts[4])
            except ValueError:
                continue
            date_str = parts[5].strip() if len(parts) > 5 else ""

            if isin and isin != "-":
                parsed[isin] = {
                    "nav": nav,
                    "name": name,
                    "scheme_code": code,
                    "date": date_str
                }
    
    _cache_navs = parsed
    log.info(f"Loaded {len(parsed)} MF schemes from AMFI.")
    return parsed

def get_nav_by_isin(isin):
    data = fetch_amfi_data()
    return data.get(isin, {}).get("nav")
