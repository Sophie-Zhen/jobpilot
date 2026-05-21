"""Telegram bot daemon — receives button taps from digest cards.

Phase 2 redesign: digest cards now carry two buttons — Save / Skip.
``Save`` bookmarks the job to ``data/saved.json`` for later attention
(Phase 3 will consume this as the input list for tailoring). ``Skip``
appends to ``data/skipped.json`` so the job is never re-digested.

Long-polling, run via ``jobpilot bot run``. Chat-id whitelisted to the
TELEGRAM_CHAT_ID env var; messages from any other chat are silently
dropped.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

_LOG = logging.getLogger("jobpilot.bot")

# ---------------------------------------------------------------------------
# Callback data encoding
# ---------------------------------------------------------------------------

# Telegram caps callback_data at 64 bytes. Encoded as "<action>:<job_id>";
# job IDs can be ~40 chars (e.g. "opencli_linkedin_4414676375") so action
# codes stay short.
ACTION_SAVE = "sv"
ACTION_SKIP = "x"

_BUTTON_LABELS = {
    ACTION_SAVE: "Saved",
    ACTION_SKIP: "Skipped",
}


def build_card_markup(job_id: str) -> InlineKeyboardMarkup:
    """Inline keyboard for a digest card — Save | Skip."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐ Save", callback_data=f"{ACTION_SAVE}:{job_id}"),
        InlineKeyboardButton("Skip",    callback_data=f"{ACTION_SKIP}:{job_id}"),
    ]])


def build_card_markup_dict(job_id: str) -> dict[str, Any]:
    """Same keyboard as a plain dict — for urllib-based senders."""
    return {
        "inline_keyboard": [[
            {"text": "⭐ Save", "callback_data": f"{ACTION_SAVE}:{job_id}"},
            {"text": "Skip",    "callback_data": f"{ACTION_SKIP}:{job_id}"},
        ]]
    }


# ---------------------------------------------------------------------------
# Persistence helpers — saved.json, skipped.json
# ---------------------------------------------------------------------------

_SAVED_PATH = Path("data/saved.json")
_SKIPPED_PATH = Path("data/skipped.json")
_PIPELINE_PATH = Path("data/pipeline_jobs.json")


def _load_pipeline() -> list[dict[str, Any]]:
    if not _PIPELINE_PATH.exists():
        return []
    try:
        return json.loads(_PIPELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_json_list(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _record_saved(job: dict[str, Any]) -> str:
    """Append a saved-job entry. Idempotent on job_id."""
    saved = _load_json_list(_SAVED_PATH)
    job_id = job.get("id", "")
    if any(s.get("job_id") == job_id for s in saved):
        return "already_logged"
    saved.append({
        "job_id": job_id,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "url": job.get("url", ""),
        "date_saved": date.today().isoformat(),
    })
    _save_json_list(_SAVED_PATH, saved)
    return "logged"


def _record_skipped(job: dict[str, Any]) -> str:
    """Append a skipped-job entry. Idempotent on job_id."""
    skipped = _load_json_list(_SKIPPED_PATH)
    job_id = job.get("id", "")
    if any(s.get("job_id") == job_id for s in skipped):
        return "already_logged"
    skipped.append({
        "job_id": job_id,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "url": job.get("url", ""),
        "date_skipped": date.today().isoformat(),
    })
    _save_json_list(_SKIPPED_PATH, skipped)
    return "logged"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _allowed_chat_id() -> int:
    raw = os.getenv("TELEGRAM_CHAT_ID", "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _is_authorized(update: Update) -> bool:
    allowed = _allowed_chat_id()
    chat = update.effective_chat
    return bool(allowed) and chat is not None and chat.id == allowed


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    chat = update.effective_chat
    if chat is None:
        return
    await chat.send_message(
        "JobPilot bot online. Run `jobpilot digest` from your laptop to "
        "get cards here; tap Save to bookmark or Skip to drop."
    )


async def cmd_ping(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    chat = update.effective_chat
    if chat is None:
        return
    await chat.send_message("pong")


async def handle_callback(
    update: Update, _ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process Save / Skip taps on digest cards."""
    query = update.callback_query
    if query is None:
        return

    if not _is_authorized(update):
        await query.answer("unauthorized")
        _LOG.warning(
            "Rejected callback from unauthorized chat_id=%s",
            update.effective_chat.id if update.effective_chat else "?",
        )
        return

    data = query.data or ""
    action, _, job_id = data.partition(":")
    if not action or not job_id:
        await query.answer("bad payload", show_alert=True)
        return

    if action not in _BUTTON_LABELS:
        await query.answer("unknown action", show_alert=True)
        return

    pipeline = _load_pipeline()
    job = next((j for j in pipeline if j.get("id") == job_id), None)
    if job is None:
        await query.answer("job not in pipeline anymore", show_alert=True)
        return

    if action == ACTION_SAVE:
        status = _record_saved(job)
        marker = "⭐ Saved"
    else:  # ACTION_SKIP
        status = _record_skipped(job)
        marker = "✗ Skipped"

    original = query.message.text if query.message else ""
    timestamp = date.today().isoformat()
    new_text = f"{marker} ({timestamp})\n\n{original}"
    try:
        await query.edit_message_text(new_text, reply_markup=None)
    except Exception as exc:
        _LOG.warning("edit_message_text failed: %s", exc)

    note = " (already logged)" if status == "already_logged" else ""
    await query.answer(f"{marker}{note}")


# ---------------------------------------------------------------------------
# Daemon entry point
# ---------------------------------------------------------------------------

def run_bot() -> None:
    """Start long-polling. Blocks until Ctrl+C."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in environment.")
    allowed = _allowed_chat_id()
    if not allowed:
        raise RuntimeError("TELEGRAM_CHAT_ID not set or invalid.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CallbackQueryHandler(handle_callback))

    _LOG.info("Bot online, polling for callback queries...")
    app.run_polling(allowed_updates=["callback_query", "message"])


if __name__ == "__main__":
    run_bot()
