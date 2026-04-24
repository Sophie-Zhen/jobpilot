"""Lightweight notification sender. Uses urllib (no requests dependency)."""
from __future__ import annotations

import os
import urllib.parse
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def send_telegram(message: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        params = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
        })
        url = f"https://api.telegram.org/bot{token}/sendMessage?{params}"
        urllib.request.urlopen(url, timeout=10)
        return True
    except Exception:
        return False
