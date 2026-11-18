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
    "Symbol", "Shares", "Avg Buy", "CMP", 
    "Day Chg%", "1W Return %", "1M Return %", "3M Return %", "6M Return %", "12 Months",
    "Invested", "Value", "P&L", "Not > 5%", "XIRR", "Return %", "Suggested Exit Target %", "Wt %", 
    "Stop Loss", "Target", "Buy More@", "Signal"
]
SYMBOL_COL = PORTFOLIO_COLUMNS.index("Symbol")

# ══════════════════════════════════════════════
# WATCHLISTS
# ══════════════════════════════════════════════
WATCHLISTS = {
    "Future Buy": [
        "ZYDUSLIFE", "LUPIN", "SUNPHARMA", "ADANIENT", "ADANIPORTS",
        "AUBANK", "AUROPHARMA", "GLAND", "DRREDDY", "DIVISLAB",
        "BIOCON", "AJANTPHARM", "MARICO", "BHARATFORG", "ICICIBANK",
        "POLYCAB", "AXISBANK", "CGPOWER", "HONAUT", "GLENMARK",
        "CUMMINSIND", "LT", "EICHERMOT", "BEL", "NHPC",
        "FORTIS", "BHARTIARTL", "CIPLA", "HAL", "ADANIPOWER",
        "MOTILALOFS", "NTPC", "MRF", "DMART", "OIL",
        "NATIONALUM", "HINDPETRO", "BPCL", "BLUESTARCO", "MAZDOCK",
        "ABBOTINDIA", "TECHM", "MPHASIS", "MUTHOOTFIN", "HINDZINC",
        "HAVELLS", "COCHINSHIP", "GLAXO", "JWL", "HINDCOPPER",
        "HCLTECH", "KPITTECH", "MEDPLUS", "ALKYLAMINE", "LAURUSLABS",
        "TTKPRESTIG", "JYOTHYLAB", "JKPAPER", "MASTEK", "WOCKPHARMA",
        "DATAPATTNS", "LTFOODS", "GILLETTE", "NESTLEIND", "SUNDARMFIN",
        "SHRIRAMFIN", "PNBHOUSING", "CHOLAHLDNG", "IDFCFIRSTB", "MANAPPURAM",
        "UJJIVANSFB", "KTKBANK", "ANGELONE", "EQUITASBNK", "BANDHANBNK",
        "ARMANFIN", "INDUSINDBK", "SUPRIYA", "APOLLOHOSP", "KIMS",
        "MAXHEALTH", "LUXIND", "IRFC", "ATGL", "JPPOWER",
        "SRF", "FINEORG", "LALPATHLAB", "VINATIORGA", "THYROCARE",
        "BSOFT", "BERGEPAINT", "TIMKEN", "PIDILITIND", "ABB",
        "ABCAPITAL", "ABSLAMC", "AMBUJACEM", "BANKBARODA", "CHOLAFIN",
        "GODREJCP", "GRASIM", "INDUSTOWER", "JINDALSTEL", "MOTHERSON",
        "SAIL", "SBILIFE", "SBIN", "SCHNEIDER", "TATACHEM",
        "TATAPOWER", "TITAN", "TORNTPHARM", "TRENT", "TVSMOTOR",
        "ULTRACEMCO", "WELCORP", "BAJAJFINSV", "PVRINOX", "CRISIL",
        "CAMS", "ETERNAL",
    ],
}
