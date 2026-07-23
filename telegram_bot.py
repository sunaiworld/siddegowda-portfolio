#!/usr/bin/env python3
"""
Single-shot Telegram command poller.
No always-on process — GitHub Actions cron runs this file every
~5 minutes. Each run: fetch any new updates since the stored offset,
reply to each, persist the new offset, exit.

/refresh is the only command that touches the live pipeline
(main.run_portfolio_update); everything else reads cached Sheets data
or does a lightweight yfinance lookup.
"""
import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode

from config import BOT_TOKEN, CHAT_ID
from services.sheets_state import get_offset, set_offset, _get_sheet
import handlers
from src.main import run_portfolio_update, build_alert_message, send_telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


async def dispatch(text):
    """Routes a command string to the right handler. Never raises —
    catches per-command so one bad command doesn't kill the run."""
    text = text.strip()
    cmd, _, rest = text.partition(" ")
    cmd = cmd.lower()

    try:
        if cmd == "/start":
            return handlers.handle_start()
        if cmd == "/help":
            return handlers.handle_help()
        if cmd == "/portfolio":
            return handlers.handle_portfolio()
        if cmd == "/buy":
            return handlers.handle_buy()
        if cmd == "/sell":
            return handlers.handle_sell()
        if cmd == "/top":
            return handlers.handle_top()
        if cmd == "/price":
            return handlers.handle_price(rest)
        if cmd == "/news":
            return handlers.handle_news(rest.split()[0] if rest.strip() else "")
        if cmd == "/refresh":
            return await handle_refresh()
        return "Unknown command. Send /help for the command list."
    except Exception as e:
        log.exception(f"Handler failed for '{text}'")
        return f"❌ Something went wrong handling that command: {e}"


async def handle_refresh():
    """Runs the same pipeline main.py's cron uses, reusing
    run_portfolio_update() — no duplicated logic."""
    try:
        sh = _get_sheet()
        out = run_portfolio_update(sh)
        if out is None:
            return "❌ Refresh failed — no symbols found in Portfolio tab."

        msg = build_alert_message(out["alerts"], out["portfolio_live_value"], out["top_picks"])
        if len(msg) > 4000:
            msg = msg[:4000] + "\n\n...truncated"
        send_telegram(msg)  # existing summary push, unchanged

        return "✅ Portfolio refreshed successfully."
    except Exception as e:
        log.exception("Refresh failed")
        return f"❌ Refresh failed: {e}"


async def main():
    bot = Bot(token=BOT_TOKEN)
    offset = get_offset()
    log.info(f"Polling from offset {offset}")

    updates = await bot.get_updates(offset=offset, timeout=0)
    if not updates:
        log.info("No new updates.")
        return

    last_id = offset
    for update in updates:
        last_id = update.update_id + 1
        msg = update.message
        if not msg or not msg.text:
            continue

        chat_id = msg.chat_id
        log.info(f"Command from {chat_id}: {msg.text}")

        reply = await dispatch(msg.text)
        # Telegram has a 4096 char limit. Split by lines to avoid breaking markdown.
        lines = reply.split('\n')
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 4000:
                try:
                    await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    await bot.send_message(chat_id=chat_id, text=chunk)
                chunk = line + "\n"
            else:
                chunk += line + "\n"
        
        if chunk.strip():
            try:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await bot.send_message(chat_id=chat_id, text=chunk)

    set_offset(last_id)
    log.info(f"Offset advanced to {last_id}")


if __name__ == "__main__":
    asyncio.run(main())