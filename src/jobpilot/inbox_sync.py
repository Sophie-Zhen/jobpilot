"""Phase 4: multi-account Gmail inbox sync.

Reads recent messages from each configured Gmail account, asks the LLM to
classify each one (rejection / interview_invite / info_request / ack / other),
matches against ``data/applications.json``, and updates status_history.
Optionally pushes a Telegram card per status change.

Hard rule: read-only on the email side. Never composes, replies, or labels.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CONFIG_DIR = Path.home() / ".config" / "jobpilot"
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
ACCOUNTS_CONFIG = CONFIG_DIR / "inbox_accounts.json"

INBOX_STATE_PATH = Path("data/inbox_state.json")
APPLICATIONS_PATH = Path("data/applications.json")

# Phase 4 (manual-mode application status sync) query.
# Two clauses joined by OR:
#  (1) Known-good sender domains (ATS providers + careers@/recruiting@/etc inboxes)
#  (2) Subject phrases that signal real application correspondence
# Then hard-exclude pure job-alert senders (those belong to Phase 5 inbox-discover).
# Keep jobs-noreply@linkedin.com NOT excluded — its "was sent to" acks are useful;
# its "apply now" alerts won't match clause (1) or (2) so they get filtered anyway.
DEFAULT_QUERY = (
    "("
    "from:(*@us.greenhouse-mail.io OR *@hire.lever.co OR *@ashbyhq.com "
    "OR *@myworkday.com OR *@workable.com OR *@smartrecruiters.com "
    "OR *@bamboohr.com OR *@dover.com OR *@email.apple.com "
    "OR careers@* OR recruiting@* OR talent@* OR people@*)"
    " OR "
    'subject:("your application" OR "your application to" '
    'OR "application update" OR "application status" '
    'OR "regarding your application" OR "regarding your" '
    'OR "your interview" OR "next steps" OR "moving forward" '
    'OR "regret to inform" OR "thank you for applying" '
    'OR "thank you for your application" OR "we received your application" '
    'OR "was sent to" OR "an update on your application")'
    ")"
    " -from:(jobalerts-noreply@linkedin.com OR newsletters-noreply@linkedin.com "
    "OR updates-noreply@linkedin.com OR noreply@glassdoor.com "
    "OR no-reply@twinehq.com)"
    " newer_than:30d"
)

CATEGORY_TO_STATUS = {
    "rejection": "rejection",
    "interview_invite": "interview",
    "info_request": "info_requested",
    # ack + other do not change status; recorded as note only
}


# --- Data shapes ----------------------------------------------------------


@dataclass
class EmailMessage:
    account: str  # which gmail account this came from
    msg_id: str  # Gmail API message ID
    message_id_header: str  # RFC 2822 Message-ID for cross-account dedup
    thread_id: str
    from_addr: str
    from_name: str
    to_addr: str
    subject: str
    body: str
    date: datetime
    labels: list[str] = field(default_factory=list)
    snippet: str = ""


@dataclass
class Classification:
    category: str
    company_guess: str
    role_guess: str
    confidence: float
    rationale: str


# --- Account config -------------------------------------------------------


def load_accounts() -> list[dict]:
    """Return list of ``{email, token}`` dicts from inbox_accounts.json."""
    if not ACCOUNTS_CONFIG.exists():
        raise FileNotFoundError(
            f"Missing inbox accounts config: {ACCOUNTS_CONFIG}\n"
            f'Create one shaped like: {{"accounts": '
            f'[{{"email": "you@gmail.com", "token": "token_you.json"}}]}}'
        )
    cfg = json.loads(ACCOUNTS_CONFIG.read_text(encoding="utf-8"))
    accounts = cfg.get("accounts", [])
    if not accounts:
        raise ValueError(f"No accounts listed in {ACCOUNTS_CONFIG}")
    return accounts


# --- State ----------------------------------------------------------------


def load_inbox_state() -> dict:
    if not INBOX_STATE_PATH.exists():
        return {"per_account": {}, "processed_message_ids": []}
    try:
        return json.loads(INBOX_STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"per_account": {}, "processed_message_ids": []}


def save_inbox_state(state: dict) -> None:
    INBOX_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Cap processed_message_ids ring buffer to avoid unbounded growth.
    pmi = state.get("processed_message_ids", [])
    if len(pmi) > 5000:
        state["processed_message_ids"] = pmi[-5000:]
    INBOX_STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# --- OAuth / service ------------------------------------------------------


def _build_service(account: dict):
    """Return a Gmail API service for one account. Triggers consent if missing."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = CONFIG_DIR / account["token"]
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not CREDENTIALS_PATH.exists():
            raise FileNotFoundError(
                f"Missing OAuth credentials at {CREDENTIALS_PATH}. "
                "Download from Google Cloud Console > Credentials > OAuth client ID."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(CREDENTIALS_PATH), SCOPES
        )
        creds = flow.run_local_server(
            port=0, prompt="consent", login_hint=account["email"]
        )
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# --- Message parsing ------------------------------------------------------


def _walk_parts(payload: dict) -> Iterator[dict]:
    if "parts" in payload:
        for p in payload["parts"]:
            yield from _walk_parts(p)
    else:
        yield payload


def _extract_body(payload: dict) -> str:
    """Prefer text/plain leaves; fall back to text/html with tags stripped."""
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in _walk_parts(payload):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if not data:
            continue
        try:
            decoded = base64.urlsafe_b64decode(data + "==").decode(
                "utf-8", errors="replace"
            )
        except Exception:
            continue
        if mime == "text/plain":
            plain_parts.append(decoded)
        elif mime == "text/html":
            html_parts.append(decoded)
    if plain_parts:
        return _normalize_whitespace("\n\n".join(plain_parts))
    if html_parts:
        stripped = re.sub(r"<[^>]+>", " ", "\n".join(html_parts))
        return _normalize_whitespace(stripped)
    return ""


def _normalize_whitespace(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_from(from_header: str) -> tuple[str, str]:
    """Return (display_name, email_addr) from a From header."""
    m = re.match(r'(?:"?([^"<]+?)"?\s+)?<?([^<>\s]+@[^<>\s]+)>?', from_header)
    if not m:
        return "", from_header.strip()
    return (m.group(1) or "").strip(), (m.group(2) or "").strip()


def _parse_message(service, account_email: str, msg_id: str) -> EmailMessage:
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=msg_id, format="full")
        .execute()
    )
    headers = {
        h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])
    }
    from_name, from_addr = _parse_from(headers.get("from", ""))
    try:
        msg_date = parsedate_to_datetime(headers.get("date", ""))
    except Exception:
        msg_date = datetime.now(timezone.utc)
    return EmailMessage(
        account=account_email,
        msg_id=msg_id,
        message_id_header=headers.get("message-id", ""),
        thread_id=msg.get("threadId", ""),
        from_addr=from_addr,
        from_name=from_name,
        to_addr=headers.get("to", ""),
        subject=headers.get("subject", ""),
        body=_extract_body(msg["payload"]),
        date=msg_date,
        labels=msg.get("labelIds", []),
        snippet=msg.get("snippet", ""),
    )


# --- Fetch ----------------------------------------------------------------


def fetch_account_messages(
    account: dict, query: str, max_results: int = 100
) -> list[EmailMessage]:
    service = _build_service(account)
    resp = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )
    ids = [m["id"] for m in resp.get("messages", [])]
    out: list[EmailMessage] = []
    for mid in ids:
        try:
            out.append(_parse_message(service, account["email"], mid))
        except Exception as exc:
            print(
                f"  WARN: failed to parse {mid} on {account['email']}: {exc}"
            )
    return out


def fetch_all_accounts(
    query: str,
    state: dict,
    account_filter: str | None = None,
    max_results: int = 100,
) -> list[EmailMessage]:
    """Fetch from all accounts, dedup by Message-ID header (cross-account safe)."""
    seen: set[str] = set(state.get("processed_message_ids", []))
    seen_this_run: set[str] = set()
    merged: list[EmailMessage] = []
    for account in load_accounts():
        if account_filter and account["email"] != account_filter:
            continue
        try:
            msgs = fetch_account_messages(account, query, max_results=max_results)
        except Exception as exc:
            print(f"  ERROR fetching {account['email']}: {exc}")
            continue
        print(f"  {account['email']}: {len(msgs)} messages matched query")
        for m in msgs:
            dedup_key = m.message_id_header or f"{m.account}:{m.msg_id}"
            if dedup_key in seen or dedup_key in seen_this_run:
                continue
            seen_this_run.add(dedup_key)
            merged.append(m)
    return merged


# --- Classifier -----------------------------------------------------------


def classify(msg: EmailMessage) -> Classification:
    """Ask Claude to categorize this email and extract company/role."""
    from jobpilot.llm import _call_claude, _parse_json_response

    prompt = (
        "Classify this email related to a job application. Return JSON only.\n\n"
        f"FROM: {msg.from_name} <{msg.from_addr}>\n"
        f"SUBJECT: {msg.subject}\n"
        f"DATE: {msg.date.isoformat()}\n"
        f"BODY:\n{msg.body[:3000]}\n\n"
        "Categories:\n"
        "- rejection: explicit no-go, won't move forward, position filled\n"
        "- interview_invite: scheduling call, screening, take-home test, next round\n"
        "- info_request: ask for additional info (work auth, salary expectations, docs)\n"
        "- ack: automated thank-you-for-applying / received-application receipt\n"
        "- other: newsletter, marketing, unrelated\n\n"
        "Return JSON object with fields:\n"
        '- "category": one of the categories above\n'
        '- "company": best guess at hiring company name (the employer, not the recruiter agency)\n'
        '- "role": best guess at role title, or empty string\n'
        '- "confidence": 0.0-1.0\n'
        '- "rationale": one-sentence reasoning\n\n'
        "Return ONLY the JSON object, no markdown fences."
    )
    response = _call_claude(prompt, timeout=60)
    data = _parse_json_response(response)
    return Classification(
        category=str(data.get("category", "other")).lower().strip(),
        company_guess=str(data.get("company", "")).strip(),
        role_guess=str(data.get("role", "")).strip(),
        confidence=float(data.get("confidence", 0.5)),
        rationale=str(data.get("rationale", "")).strip(),
    )


# --- Application matching -------------------------------------------------


def _normalize_company(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\b(inc|ltd|llc|gmbh|plc|limited|corporation|corp|technologies|tech|labs)\b\.?", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def match_application(
    apps: list[dict], cls: Classification, msg: EmailMessage
) -> dict | None:
    """Best-effort match by normalized company name; fall back to sender domain."""
    target = _normalize_company(cls.company_guess) if cls.company_guess else ""
    if target:
        for app in apps:
            app_norm = _normalize_company(app.get("company", ""))
            if not app_norm:
                continue
            if app_norm == target or target in app_norm or app_norm in target:
                return app
    # Fallback: sender domain → company
    domain = msg.from_addr.split("@")[-1].lower() if "@" in msg.from_addr else ""
    domain_stem = domain.split(".")[0] if domain else ""
    if domain_stem and domain_stem not in {"gmail", "noreply", "no-reply"}:
        for app in apps:
            app_norm = _normalize_company(app.get("company", ""))
            if app_norm and (domain_stem in app_norm or app_norm in domain_stem):
                return app
    return None


# --- Apply updates --------------------------------------------------------


def update_applications(
    triples: list[tuple[EmailMessage, Classification, dict]],
    dry_run: bool = False,
) -> list[dict]:
    """Mutate matched application rows. Returns list of events that produced a status change."""
    today = date.today().isoformat()
    events: list[dict] = []
    for msg, cls, app in triples:
        new_status = CATEGORY_TO_STATUS.get(cls.category)
        if not new_status:
            continue
        prev_status = app.get("status")
        if prev_status == new_status:
            continue
        if not dry_run:
            app["status"] = new_status
            history = app.setdefault("status_history", [])
            history.append(
                {
                    "status": new_status,
                    "date": today,
                    "notes": f"[auto-inbox-sync] {cls.rationale}",
                    "source": "gmail",
                    "source_account": msg.account,
                    "source_email_id": msg.msg_id,
                    "classifier_confidence": cls.confidence,
                }
            )
            dates = app.setdefault("dates", {})
            dates[new_status] = today
        events.append(
            {
                "msg": msg,
                "cls": cls,
                "app": app,
                "prev_status": prev_status,
                "new_status": new_status,
            }
        )
    return events


# --- Phase 4.5: auto-bootstrap applications from unmatched acks ----------


def bootstrap_applications(
    unmatched: list[tuple[EmailMessage, Classification]],
    apps: list[dict],
    dry_run: bool = False,
) -> list[dict]:
    """Auto-create applications.json rows from unmatched ack emails.

    Picks up LinkedIn Easy-Apply submissions and direct ATS acks that Sophie
    didn't manually log. New rows land at status=submitted with source noted.
    Idempotent within a run (dedups by normalized company) and against the
    apps list (won't double-create when a row already exists under a slightly
    different name). Cross-run dedup is handled upstream via processed_message_ids.
    """
    today = date.today().isoformat()
    events: list[dict] = []
    seen_in_run: set[str] = set()
    existing = {_normalize_company(a.get("company", "")) for a in apps if a.get("company")}

    for msg, cls in unmatched:
        if cls.category != "ack":
            continue
        company = cls.company_guess.strip()
        if not company:
            continue
        company_norm = _normalize_company(company)
        if not company_norm:
            continue
        if company_norm in seen_in_run or company_norm in existing:
            continue
        seen_in_run.add(company_norm)

        new_app = {
            "job_id": f"inbox_{msg.msg_id}",
            "company": company,
            "title": cls.role_guess or "(auto-detected, role unknown)",
            "status": "submitted",
            "source": "inbox_sync_bootstrap",
            "dates": {"submitted": today},
            "status_history": [
                {
                    "status": "submitted",
                    "date": today,
                    "notes": f"[auto-bootstrap from inbox ack] {cls.rationale}",
                    "source": "gmail",
                    "source_account": msg.account,
                    "source_email_id": msg.msg_id,
                    "classifier_confidence": cls.confidence,
                }
            ],
        }
        if not dry_run:
            apps.append(new_app)
        events.append(
            {
                "msg": msg,
                "cls": cls,
                "app": new_app,
                "prev_status": None,
                "new_status": "submitted",
                "bootstrap": True,
            }
        )
    return events


# --- Telegram push --------------------------------------------------------


def _format_event(event: dict) -> str:
    cls: Classification = event["cls"]
    app = event["app"]
    if event.get("bootstrap"):
        return (
            f"✨ NEW APPLICATION (auto-detected)\n"
            f"{app.get('company','?')} — {app.get('title','?')}\n"
            f"From: {event['msg'].from_addr}\n"
            f"Subject: {event['msg'].subject}\n"
            f"Confidence: {cls.confidence:.2f}\n"
            f"Reason: {cls.rationale}"
        )
    icon = {"rejection": "❌", "interview": "🎉", "info_requested": "📝"}.get(
        event["new_status"], "📬"
    )
    return (
        f"{icon} {event['new_status'].upper()}\n"
        f"{app.get('company','?')} — {app.get('title','?')}\n"
        f"From: {event['msg'].from_addr}\n"
        f"Subject: {event['msg'].subject}\n"
        f"Confidence: {cls.confidence:.2f}\n"
        f"Reason: {cls.rationale}"
    )


def push_event(event: dict) -> bool:
    from jobpilot.notify import send_telegram

    return send_telegram(_format_event(event), parse_mode=None)


# --- Orchestrator ---------------------------------------------------------


def run_inbox_sync(
    query: str | None = None,
    dry_run: bool = False,
    account_filter: str | None = None,
    push_telegram: bool = True,
) -> dict:
    """End-to-end: fetch → classify → match → update → push. Returns summary dict."""
    query = query or DEFAULT_QUERY
    state = load_inbox_state()

    print(f"Query: {query}")
    if dry_run:
        print("DRY RUN — no writes to applications.json or Telegram\n")

    messages = fetch_all_accounts(
        query=query, state=state, account_filter=account_filter
    )
    print(f"Fetched {len(messages)} unique messages across accounts")
    if not messages:
        return {"messages": 0, "classified": 0, "matched": 0, "events": 0}

    apps = (
        json.loads(APPLICATIONS_PATH.read_text(encoding="utf-8"))
        if APPLICATIONS_PATH.exists()
        else []
    )

    triples: list[tuple[EmailMessage, Classification, dict]] = []
    unmatched: list[tuple[EmailMessage, Classification]] = []
    for i, msg in enumerate(messages, 1):
        print(f"  [{i}/{len(messages)}] {msg.subject[:60]}  from {msg.from_addr}")
        try:
            cls = classify(msg)
        except Exception as exc:
            print(f"    classify failed: {exc}")
            continue
        print(f"    -> {cls.category} (conf={cls.confidence:.2f}) {cls.company_guess}")
        app = match_application(apps, cls, msg)
        if app is None:
            unmatched.append((msg, cls))
            continue
        triples.append((msg, cls, app))

    events = update_applications(triples, dry_run=dry_run)
    bootstrap_events = bootstrap_applications(unmatched, apps, dry_run=dry_run)
    all_events = events + bootstrap_events

    bootstrapped_msg_ids = {e["msg"].msg_id for e in bootstrap_events}
    truly_unmatched = [
        (m, c) for m, c in unmatched if m.msg_id not in bootstrapped_msg_ids
    ]

    if not dry_run:
        if all_events:
            APPLICATIONS_PATH.write_text(
                json.dumps(apps, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        # Record processed message IDs so we don't reprocess on next run.
        pmi = state.setdefault("processed_message_ids", [])
        for m in messages:
            key = m.message_id_header or f"{m.account}:{m.msg_id}"
            if key not in pmi:
                pmi.append(key)
        state["last_sync_at"] = datetime.now(timezone.utc).isoformat()
        save_inbox_state(state)

    if push_telegram and not dry_run:
        for event in all_events:
            push_event(event)

    summary = {
        "messages": len(messages),
        "classified": len(triples) + len(unmatched),
        "matched": len(triples),
        "events": len(events),
        "bootstrapped": len(bootstrap_events),
        "unmatched_count": len(truly_unmatched),
    }
    print(f"\nSummary: {summary}")
    if bootstrap_events:
        print("\nAuto-added applications from unmatched acks:")
        for event in bootstrap_events:
            app = event["app"]
            print(f"  + {app['company']} — {app['title']}")
    if truly_unmatched:
        print("\nUnmatched (no application row found, no action taken):")
        for msg, cls in truly_unmatched[:10]:
            print(f"  - {cls.category} | {cls.company_guess} | {msg.subject[:50]}")
    return summary
