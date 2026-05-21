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


def send_telegram(message: str, parse_mode: str | None = "Markdown") -> bool:
    """Send a Telegram message. Returns True on success.

    ``parse_mode``: Telegram parse mode ("Markdown", "MarkdownV2", "HTML")
    or ``None`` for plain text. Pass ``None`` when the message contains
    untrusted content (job descriptions, user text) that may include
    unbalanced markdown delimiters — those raise 400 "can't parse entities"
    from Telegram. Default is "Markdown" for backwards compatibility with
    callers that send composed status messages.
    """
    import sys
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        body: dict[str, str] = {"chat_id": chat_id, "text": message}
        if parse_mode:
            body["parse_mode"] = parse_mode
        params = urllib.parse.urlencode(body)
        url = f"https://api.telegram.org/bot{token}/sendMessage?{params}"
        urllib.request.urlopen(url, timeout=10)
        return True
    except Exception as exc:
        # Surface the failure to stderr so debugging doesn't require
        # adding logging at every call site. Callers can suppress by
        # redirecting stderr if the noise is unwanted.
        print(f"send_telegram failed: {exc}", file=sys.stderr)
        return False
