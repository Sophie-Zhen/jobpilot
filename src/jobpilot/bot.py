"""Telegram bot daemon — receives button taps from digest cards.

Phase 2: digest cards carry two buttons — Save / Skip. ``Skip`` appends to
``data/skipped.json`` so the job is never re-digested.

Phase 3: tapping ``Save`` bookmarks the job to ``data/saved.json`` AND swaps
the card's keyboard to the disposition row — ``⚙️ Tailor`` /
``✅ Applied +Cover`` / ``✅ Applied −Cover`` / ``🗑 Drop`` — so the whole
post-save workflow happens from Telegram instead of hand-edited JSON:

  - ``Tailor`` runs ``jobpilot tailor --job-id`` in a subprocess and sends
    the resulting CV + cover-letter PDFs back to the chat.
  - ``Applied +/−Cover`` writes the submission to ``data/applications.json``
    (``cover_letter_uploaded`` true/false) and removes it from saved.json.
  - ``Drop`` moves the saved job to skipped.json (decided not to apply).

Long-polling, run via ``jobpilot bot run``. Chat-id whitelisted to the
TELEGRAM_CHAT_ID env var; messages from any other chat are silently
dropped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
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
ACTION_TAILOR = "t"
ACTION_APPLIED_CL = "a1"   # applied, cover letter uploaded
ACTION_APPLIED_NOCL = "a0"  # applied, no cover letter
ACTION_DROP = "dr"          # saved-then-decided-not-to-apply → skipped

# Terminal actions lock the card with this marker and remove the keyboard.
_TERMINAL_LABELS = {
    ACTION_SKIP: "✗ Skipped",
    ACTION_APPLIED_CL: "✅ Applied (+ cover letter)",
    ACTION_APPLIED_NOCL: "✅ Applied (no cover letter)",
    ACTION_DROP: "🗑 Dropped",
}

# Every action the callback router accepts (terminal + non-terminal).
_KNOWN_ACTIONS = {
    ACTION_SAVE, ACTION_SKIP, ACTION_TAILOR,
    ACTION_APPLIED_CL, ACTION_APPLIED_NOCL, ACTION_DROP,
}


def build_card_markup(job_id: str) -> InlineKeyboardMarkup:
    """Inline keyboard for a fresh digest card — Save | Skip."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⭐ Save", callback_data=f"{ACTION_SAVE}:{job_id}"),
        InlineKeyboardButton("Skip",    callback_data=f"{ACTION_SKIP}:{job_id}"),
    ]])


def build_card_markup_dict(job_id: str) -> dict[str, Any]:
    """Same fresh-card keyboard as a plain dict — for urllib-based senders."""
    return {
        "inline_keyboard": [[
            {"text": "⭐ Save", "callback_data": f"{ACTION_SAVE}:{job_id}"},
            {"text": "Skip",    "callback_data": f"{ACTION_SKIP}:{job_id}"},
        ]]
    }


def build_post_save_markup(job_id: str) -> InlineKeyboardMarkup:
    """Disposition keyboard shown after a job is Saved (Phase 3)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Tailor", callback_data=f"{ACTION_TAILOR}:{job_id}")],
        [
            InlineKeyboardButton("✅ Applied +Cover", callback_data=f"{ACTION_APPLIED_CL}:{job_id}"),
            InlineKeyboardButton("✅ Applied −Cover", callback_data=f"{ACTION_APPLIED_NOCL}:{job_id}"),
        ],
        [InlineKeyboardButton("🗑 Drop", callback_data=f"{ACTION_DROP}:{job_id}")],
    ])


# ---------------------------------------------------------------------------
# Persistence helpers — saved.json, skipped.json
# ---------------------------------------------------------------------------

_SAVED_PATH = Path("data/saved.json")
_SKIPPED_PATH = Path("data/skipped.json")
_PIPELINE_PATH = Path("data/pipeline_jobs.json")
_APPLICATIONS_PATH = Path("data/applications.json")


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


def _record_applied(job: dict[str, Any], cover_letter_uploaded: bool) -> str:
    """Append a submitted-application entry. Idempotent on job_id.

    Schema mirrors the records written by the manual triage scripts /
    ``jobpilot`` so application history stays uniform.
    """
    apps = _load_json_list(_APPLICATIONS_PATH)
    job_id = job.get("id", "")
    if any(a.get("job_id") == job_id for a in apps):
        return "already_logged"
    today = date.today().isoformat()
    apps.append({
        "job_id": job_id,
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "url": job.get("url", ""),
        "status": "submitted",
        "cover_letter_uploaded": bool(cover_letter_uploaded),
        "date_discovered": job.get("date_found") or today,
        "date_submitted": today,
    })
    _save_json_list(_APPLICATIONS_PATH, apps)
    return "logged"


def _remove_from_saved(job_id: str) -> None:
    """Drop a job from saved.json once it's been applied or dropped."""
    saved = _load_json_list(_SAVED_PATH)
    remaining = [s for s in saved if s.get("job_id") != job_id]
    if len(remaining) != len(saved):
        _save_json_list(_SAVED_PATH, remaining)


def _lookup_job(job_id: str) -> dict[str, Any] | None:
    """Resolve a job by id from the pipeline, falling back to saved.json.

    The pipeline is the source of truth (carries ``date_found`` etc.), but a
    saved job may age out of a pruned pipeline before the user finishes the
    apply flow — so fall back to the saved record, which has enough to log.
    """
    for j in _load_pipeline():
        if j.get("id") == job_id:
            return j
    for s in _load_json_list(_SAVED_PATH):
        if s.get("job_id") == job_id:
            return {
                "id": s.get("job_id", ""),
                "title": s.get("title", ""),
                "company": s.get("company", ""),
                "url": s.get("url", ""),
                "date_found": s.get("date_saved"),
            }
    return None


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


def _note(status: str) -> str:
    return " (already logged)" if status == "already_logged" else ""


async def _restamp(query, marker: str, reply_markup) -> None:
    """Prepend a status marker to the card text and set/clear its keyboard."""
    original = query.message.text if query.message else ""
    new_text = f"{marker} ({date.today().isoformat()})\n\n{original}"
    try:
        await query.edit_message_text(new_text, reply_markup=reply_markup)
    except Exception as exc:
        _LOG.warning("edit_message_text failed: %s", exc)


# Job ids currently being tailored — guards against double-taps spawning
# two concurrent (expensive) tailor subprocesses for the same job.
_TAILOR_INFLIGHT: set[str] = set()


async def _run_tailor_and_send(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, job: dict[str, Any]
) -> None:
    """Run ``jobpilot tailor --job-id`` and send the PDFs back to the chat.

    Uses ``create_subprocess_exec`` so the ~2-min tailor run does not block
    the polling event loop. Default variant ``tech_eng``.
    """
    from jobpilot.cli import _job_folder
    from jobpilot.config import load_settings

    job_id = job.get("id", "")
    variant = "tech_eng"
    chat = update.effective_chat
    if chat is None:
        return
    if job_id in _TAILOR_INFLIGHT:
        await chat.send_message("⚙️ Already tailoring that one — hang on…")
        return

    _TAILOR_INFLIGHT.add(job_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "jobpilot.cli", "tailor", "--job-id", job_id,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode("utf-8", "replace")

        folder = Path(load_settings().output_dir) / _job_folder(job)
        cv = folder / f"cv_{variant}.pdf"
        cl = folder / f"cover_letter_{variant}.pdf"

        if proc.returncode != 0 or not cv.exists():
            tail = "\n".join(text.strip().splitlines()[-6:])[:500]
            await chat.send_message(
                f"⚠️ Tailor failed for {job.get('company', '?')}:\n{tail}"
            )
            return

        pages_line = next((l.strip() for l in text.splitlines() if "pages:" in l), "")
        caption = f"{job.get('company', '?')} — {job.get('title', '?')}\n{pages_line}".strip()
        with cv.open("rb") as fh:
            await ctx.bot.send_document(chat.id, document=fh, filename=cv.name, caption=caption)
        if cl.exists():
            with cl.open("rb") as fh:
                await ctx.bot.send_document(chat.id, document=fh, filename=cl.name)
    except Exception as exc:
        _LOG.warning("tailor/send failed for %s: %s", job_id, exc)
        await chat.send_message(f"⚠️ Tailor error: {exc}")
    finally:
        _TAILOR_INFLIGHT.discard(job_id)


async def handle_callback(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route digest-card taps: Save / Skip / Tailor / Applied± / Drop."""
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
    if action not in _KNOWN_ACTIONS:
        await query.answer("unknown action", show_alert=True)
        return

    job = _lookup_job(job_id)
    if job is None:
        await query.answer("job not found anymore", show_alert=True)
        return

    if action == ACTION_SAVE:
        status = _record_saved(job)
        await _restamp(query, "⭐ Saved", build_post_save_markup(job_id))
        await query.answer(f"⭐ Saved{_note(status)} — tap Tailor or Applied when ready")
        return

    if action == ACTION_TAILOR:
        await query.answer("⚙️ Tailoring… ~2 min, PDFs coming")
        await _run_tailor_and_send(update, ctx, job)
        return

    if action in (ACTION_APPLIED_CL, ACTION_APPLIED_NOCL):
        status = _record_applied(job, cover_letter_uploaded=(action == ACTION_APPLIED_CL))
        _remove_from_saved(job_id)
        marker = _TERMINAL_LABELS[action]
        await _restamp(query, marker, None)
        await query.answer(f"{marker}{_note(status)}")
        return

    if action == ACTION_DROP:
        status = _record_skipped(job)
        _remove_from_saved(job_id)
        await _restamp(query, _TERMINAL_LABELS[ACTION_DROP], None)
        await query.answer(f"🗑 Dropped{_note(status)}")
        return

    # ACTION_SKIP (fresh-card skip — terminal)
    status = _record_skipped(job)
    await _restamp(query, _TERMINAL_LABELS[ACTION_SKIP], None)
    await query.answer(f"✗ Skipped{_note(status)}")


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
