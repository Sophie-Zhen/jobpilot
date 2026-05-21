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


def send_telegram(
    message: str,
    parse_mode: str | None = "Markdown",
    reply_markup: dict | None = None,
) -> bool:
    """Send a Telegram message. Returns True on success.

    ``parse_mode``: Telegram parse mode ("Markdown", "MarkdownV2", "HTML")
    or ``None`` for plain text. Pass ``None`` when the message contains
    untrusted content (job descriptions, user text) that may include
    unbalanced markdown delimiters — those raise 400 "can't parse entities"
    from Telegram. Default is "Markdown" for backwards compatibility with
    callers that send composed status messages.

    ``reply_markup``: Telegram InlineKeyboardMarkup as a dict (see
    ``bot.build_card_markup_dict``). Pass ``None`` for a plain message
    without buttons.
    """
    import json as _json
    import sys
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        body: dict[str, str] = {"chat_id": chat_id, "text": message}
        if parse_mode:
            body["parse_mode"] = parse_mode
        if reply_markup is not None:
            body["reply_markup"] = _json.dumps(reply_markup, ensure_ascii=False)
        params = urllib.parse.urlencode(body)
        url = f"https://api.telegram.org/bot{token}/sendMessage?{params}"
        urllib.request.urlopen(url, timeout=10)
        return True
    except Exception as exc:
        print(f"send_telegram failed: {exc}", file=sys.stderr)
        return False
