import os

# ══════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════
SHEET_ID         = os.environ.get("SHEET_ID", "")

BATCH_SIZE              = 50
SLEEP_BATCH             = 8
SLEEP_INFO              = 3
SLEEP_NEWS_CACHE_WRITE  = 1   # throttle consecutive news_cache.upsert() calls (429 guard)
SL_PCT                  = 0.07
TARGET_PCT              = 0.20
FUNDAMENTALS_CACHE_DAYS = 7
TECH_WORKERS            = 4   # bounded pool for fetch_technicals()+fetch_rev_growth() — same yfinance host as prices, stay conservative
NEWS_WORKERS            = 6   # bounded pool for Google News RSS fetch — different host, more headroom

# ══════════════════════════════════════════════
# PORTFOLIO SCHEMA
# ══════════════════════════════════════════════
PORTFOLIO_COLUMNS = [
    "Symbol", "Investment Source", "Shares", "Avg Buy", "CMP", "Invested", "Value", 
    "P&L", "Return %", "Wt %", "Stop Loss", "Target", "Buy More@", "Signal"
]
SYMBOL_COL = PORTFOLIO_COLUMNS.index("Symbol")

# ══════════════════════════════════════════════
# WATCHLISTS
# ══════════════════════════════════════════════
WATCHLISTS = {
    "Future Buy": [
        "ZYDUSLIFE", "LUPIN", "SUNPHARMA", "ADANIENT", "ADANIPORTS",
        "OFSS", "AUBANK", "AUROPHARMA", "GLAND", "DRREDDY",
        "DIVISLAB", "BIOCON", "AJANTPHARM", "MARICO", "BHARATFORG",
        "ICICIBANK", "POLYCAB", "AXISBANK", "CGPOWER", "HONAUT",
        "GLENMARK", "CUMMINSIND", "LT", "SIEMENS", "EICHERMOT",
        "BEL", "NHPC", "FORTIS", "BHARTIARTL", "CIPLA",
        "COALINDIA", "HAL", "ADANIPOWER", "MOTILALOFS", "BRITANNIA",
        "NTPC", "BSE", "MRF", "DMART", "OIL",
        "ONGC", "NATIONALUM", "HINDPETRO", "BPCL", "BLUESTARCO",
        "DABUR", "MAZDOCK", "ABBOTINDIA", "TECHM", "MPHASIS",
        "MUTHOOTFIN", "COFORGE", "HINDZINC", "HAVELLS", "COCHINSHIP",
        "GLAXO", "JWL", "HINDCOPPER", "HCLTECH", "KPITTECH",
        "MEDPLUS", "ALKYLAMINE", "LAURUSLABS", "TTKPRESTIG", "JYOTHYLAB",
        "JKPAPER", "MASTEK", "WOCKPHARMA", "DATAPATTNS", "LTFOODS",
        "GILLETTE", "NESTLEIND", "SUNDARMFIN", "SHRIRAMFIN", "PNBHOUSING",
        "CHOLAHLDNG", "IDFCFIRSTB", "MANAPPURAM", "UJJIVANSFB", "KTKBANK",
        "ANGELONE", "EQUITASBNK", "BANDHANBNK", "ARMANFIN", "INDUSINDBK",
        "SUPRIYA", "APOLLOHOSP", "KIMS", "AAVAS", "CUB",
        "MAXHEALTH", "LUXIND", "IRFC", "ATGL", "JPPOWER",
        "NUVAMA", "SRF", "CANBK", "SYMPHONY", "FINEORG",
        "LALPATHLAB", "WIPRO", "VINATIORGA", "THYROCARE", "WHIRLPOOL",
        "BSOFT", "BERGEPAINT", "VOLTAS", "TIMKEN", "KSOLVES",
        "PIDILITIND",
    ],
}
