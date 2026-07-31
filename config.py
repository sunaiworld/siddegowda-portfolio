import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
BUY_MORE_DROP_PCT       = 0.10  # Buy More@ = Avg Buy × (1 − this). No existing averaging-down
                                 # rule found anywhere in the codebase — this is the new default
                                 # (10% correction from Avg Buy). Change only here.
