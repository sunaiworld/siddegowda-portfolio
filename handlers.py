from services.sheets_state import read_cached_rows, last_updated
from services.price_service import get_price_snapshot

def handle_start():
    return (
        "🤖 *Portfolio Bot Online*\n\n"
        "I can read your cached Google Sheets data instantly, or do live lookups.\n"
        "Use /help to see all commands."
    )

def handle_help():
    return (
        "📋 *Available Commands*\n\n"
        "📊 *Cached Data* (Instant):\n"
        "/portfolio - Summary of your current holdings\n"
        "/buy - Current BUY recommendations\n"
        "/sell - Current SELL recommendations\n"
        "/top - Top 5 high-scoring stocks\n\n"
        "⚡ *Live Data*:\n"
        "/price <symbol> - Live snapshot for a specific stock\n"
        "/refresh - Run the full update pipeline (takes time)"
    )

def _format_row_short(row):
    """Helper to format a short summary for a stock."""
    sym = row.get('symbol', '???')
    cmp = row.get('cmp', '0')
    action = row.get('action', '')
    score = row.get('total', '0')
    return f"• *{sym}* - ₹{cmp} | Score: {score} | {action}"

def handle_portfolio():
    rows = read_cached_rows()
    if not rows:
        return "❌ Cached data is empty or missing. Run /refresh first."
    
    updated = last_updated(rows)
    lines = [f"💼 *Portfolio Holdings* (Cached: {updated})\n"]
    for r in rows:
        lines.append(_format_row_short(r))
        
    return "\n".join(lines)

def handle_buy():
    rows = read_cached_rows()
    if not rows:
        return "❌ Cached data is empty or missing. Run /refresh first."
    
    buys = [r for r in rows if "BUY" in str(r.get("action", "")).upper()]
    if not buys:
        return "No BUY recommendations in the current cached data."
        
    updated = last_updated(rows)
    lines = [f"🟢 *BUY Recommendations* (Cached: {updated})\n"]
    for r in buys:
        lines.append(_format_row_short(r))
    return "\n".join(lines)

def handle_sell():
    rows = read_cached_rows()
    if not rows:
        return "❌ Cached data is empty or missing. Run /refresh first."
    
    sells = [r for r in rows if "SELL" in str(r.get("action", "")).upper() or "TRIM" in str(r.get("action", "")).upper()]
    if not sells:
        return "No SELL/TRIM recommendations in the current cached data."
        
    updated = last_updated(rows)
    lines = [f"🔴 *SELL/TRIM Recommendations* (Cached: {updated})\n"]
    for r in sells:
        lines.append(_format_row_short(r))
    return "\n".join(lines)

def handle_top():
    rows = read_cached_rows()
    if not rows:
        return "❌ Cached data is empty or missing. Run /refresh first."
    
    def get_score(r):
        try:
            return float(r.get("total", 0))
        except ValueError:
            return 0.0

    sorted_rows = sorted(rows, key=get_score, reverse=True)
    top_5 = sorted_rows[:5]
    
    updated = last_updated(rows)
    lines = [f"🏆 *Top 5 Stocks by Score* (Cached: {updated})\n"]
    for r in top_5:
        lines.append(_format_row_short(r))
    return "\n".join(lines)

def handle_price(rest):
    if not rest:
        return "❌ Please provide a symbol. Example: `/price INFY`"
    
    symbol = rest.split()[0].upper()
    data = get_price_snapshot(symbol)
    
    if not data:
        return f"❌ Could not fetch data for {symbol}. Invalid symbol or delisted."
        
    cmp = data.get("cmp", "N/A")
    pct = data.get("day_change_pct")
    pct_str = f"{pct}%" if pct is not None else "N/A"
    high = data.get("high52", "N/A")
    low = data.get("low52", "N/A")
    pe = data.get("pe", "N/A")
    mcap = data.get("mcap_cr", "N/A")
    
    return (
        f"📈 *{symbol} Live Snapshot*\n\n"
        f"Price: ₹{cmp} ({pct_str})\n"
        f"52W H/L: ₹{high} / ₹{low}\n"
        f"P/E: {pe}\n"
        f"Mkt Cap: ₹{mcap} Cr"
    )
