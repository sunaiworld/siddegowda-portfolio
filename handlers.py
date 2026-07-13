"""
Telegram command handlers. Each function takes (args_text) and
returns the reply string. Kept sync + pure-text so telegram_bot.py's
async loop just calls these and sends the result — no PTB coupling
here, easy to unit test.
"""
from datetime import datetime
from services.sheets_state import read_cached_rows, last_updated
from services.price_service import get_price_snapshot
from config import BOT_NAME

COMMANDS_HELP = {
    "/start": "Welcome message and command list.",
    "/help": "Shows this help.",
    "/portfolio": "Portfolio value, P&L, holdings count, BUY/SELL counts, last update.",
    "/buy": "All stocks currently BUY / STRONG BUY.",
    "/sell": "All stocks currently SELL / AVOID.",
    "/top": "Top 5 stocks by AI Score.",
    "/price <symbol>": "Live price + fundamentals for one symbol, e.g. /price HDFCBANK",
    "/refresh": "Runs the full pipeline now: prices, scores, sheet write, summary.",
}


def handle_start():
    lines = [f"👋 Welcome to *{BOT_NAME}*", "", "Available commands:"]
    lines += [f"{c} — {desc}" for c, desc in COMMANDS_HELP.items()]
    return "\n".join(lines)


def handle_help():
    lines = ["*Command Help*", ""]
    lines += [f"*{c}*\n{desc}" for c, desc in COMMANDS_HELP.items()]
    return "\n\n".join(lines)


def handle_portfolio():
    rows = read_cached_rows()
    if not rows:
        return "⚠️ No data yet — run /refresh first."

    total_value = 0.0
    buy_count = sell_count = 0
    for r in rows:
        try:
            cmp = float(str(r["cmp"]).replace(",", ""))
            total_value += cmp
        except (ValueError, TypeError):
            pass
        action = r.get("action", "")
        if action in ("STRONG BUY", "BUY"):
            buy_count += 1
        elif action in ("SELL", "AVOID"):
            sell_count += 1

    return (
        f"📊 *Portfolio Summary*\n"
        f"Holdings: {len(rows)}\n"
        f"BUY signals: {buy_count}\n"
        f"SELL/AVOID signals: {sell_count}\n"
        f"Last Update: {last_updated(rows)}\n\n"
        f"_Note: Total Portfolio Value / Today's P&L need holding qty × CMP "
        f"— not stored in GITHUB DATA tab. Say the word to wire that in from "
        f"the Portfolio tab._"
    )


def _filter_by_action(rows, actions):
    return [r for r in rows if r.get("action", "") in actions]


def handle_buy():
    rows = _filter_by_action(read_cached_rows(), ("STRONG BUY", "BUY"))
    if not rows:
        return "No BUY / STRONG BUY signals right now."
    lines = ["🟢 *BUY / STRONG BUY*", ""]
    for r in rows:
        lines.append(
            f"*{r['symbol']}* — Score {r['total']} | CMP ₹{r['cmp']} | "
            f"Sector: {r['sector']}"
        )
    return "\n".join(lines)


def handle_sell():
    rows = _filter_by_action(read_cached_rows(), ("SELL", "AVOID"))
    if not rows:
        return "No SELL / AVOID signals right now."
    lines = ["🔴 *SELL / AVOID*", ""]
    for r in rows:
        reason = r.get("weaknesses", "") or "—"
        lines.append(
            f"*{r['symbol']}* — Score {r['total']} | CMP ₹{r['cmp']} | "
            f"Reason: {reason}"
        )
    return "\n".join(lines)


def handle_top():
    rows = read_cached_rows()
    if not rows:
        return "⚠️ No data yet — run /refresh first."

    def score(r):
        try:
            return float(r.get("total", 0))
        except (ValueError, TypeError):
            return 0

    top5 = sorted(rows, key=score, reverse=True)[:5]
    lines = ["🏆 *Top 5 by AI Score*", ""]
    for i, r in enumerate(top5, 1):
        lines.append(f"{i}. {r['symbol']}\nScore {r['total']}")
    return "\n".join(lines)


def handle_price(args_text):
    if not args_text.strip():
        return "Usage: /price <symbol>\nExample: /price HDFCBANK"
    symbol = args_text.strip().split()[0]
    data = get_price_snapshot(symbol)
    if data is None:
        return f"❌ Couldn't find data for '{symbol}'. Check the NSE symbol and try again."

    return (
        f"📈 *{data['symbol']}*\n"
        f"CMP: ₹{data['cmp']}\n"
        f"52W High: ₹{data['high52'] or 'N/A'}\n"
        f"52W Low: ₹{data['low52'] or 'N/A'}\n"
        f"PE: {data['pe'] or 'N/A'}\n"
        f"Mkt Cap: ₹{data['mcap_cr'] or 'N/A'} Cr"
    )