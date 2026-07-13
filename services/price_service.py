"""
/price <symbol> — live single-symbol lookup. Reuses main.py's
fetch_fundamentals() and fetch_technicals(); no full pipeline run,
no sheet write.
"""
import logging
from src.main import fetch_fundamentals, fetch_technicals

log = logging.getLogger(__name__)


def get_price_snapshot(symbol):
    """
    Returns a dict of price/fundamental fields for `symbol`, or None
    if the symbol has no data (invalid/delisted). Never raises —
    caller should treat None as "invalid symbol".
    """
    symbol = symbol.strip().upper()
    try:
        f = fetch_fundamentals(symbol)
        tech = fetch_technicals(symbol)
    except Exception as e:
        log.warning(f"/price failed for {symbol}: {e}")
        return None

    cmp = tech.get("cmp_tech")
    if cmp is None and not f:
        return None

    high52, low52 = f.get("high52"), f.get("low52")
    day_change_pct = None
    if cmp and tech.get("sma50"):
        try:
            day_change_pct = round((cmp - tech["sma50"]) / tech["sma50"] * 100, 2)
        except ZeroDivisionError:
            day_change_pct = None

    return {
        "symbol": symbol,
        "cmp": cmp,
        "day_change_pct": day_change_pct,
        "high52": high52,
        "low52": low52,
        "pe": f.get("pe"),
        "mcap_cr": f.get("mcap_cr"),
    }