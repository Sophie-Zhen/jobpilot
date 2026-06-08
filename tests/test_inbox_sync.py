"""Tests for src/jobpilot/inbox_sync.py.

Stays offline: no Gmail API calls, no LLM calls. The LLM classifier is exercised
by monkey-patching ``_call_claude`` to return canned JSON.
"""
import base64
import json
from datetime import datetime, timezone

import pytest

from jobpilot import inbox_sync
from jobpilot.inbox_sync import (
    AuthExpired,
    Classification,
    EmailMessage,
    _extract_body,
    _format_event,
    _normalize_company,
    _parse_from,
    _walk_parts,
    bootstrap_applications,
    classify,
    match_application,
    push_auth_expired_alert,
    update_applications,
)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


# --- Pure helpers ---------------------------------------------------------


class TestNormalizeCompany:
    def test_strips_legal_suffix(self):
        assert _normalize_company("Bending Spoons Ltd.") == "bending spoons"

    def test_lowercases_and_collapses_whitespace(self):
        assert _normalize_company("  TINES  ") == "tines"

    def test_strips_punctuation(self):
        assert _normalize_company("Stripe, Inc.") == "stripe"


class TestParseFrom:
    def test_with_display_name(self):
        assert _parse_from('"Acme Team" <noreply@acme.com>') == (
            "Acme Team",
            "noreply@acme.com",
        )

    def test_bare_address(self):
        assert _parse_from("noreply@acme.com") == ("", "noreply@acme.com")

    def test_unquoted_name(self):
        assert _parse_from("Acme Recruiting <hello@acme.com>") == (
            "Acme Recruiting",
            "hello@acme.com",
        )


class TestExtractBody:
    def test_prefers_plain_text(self):
        payload = {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Hello plain")}},
                {"mimeType": "text/html", "body": {"data": _b64("<b>Hello html</b>")}},
            ]
        }
        assert _extract_body(payload) == "Hello plain"

    def test_falls_back_to_html_stripped(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _b64("<p>Hi there</p>")},
        }
        assert _extract_body(payload) == "Hi there"

    def test_nested_multipart(self):
        payload = {
            "parts": [
                {
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": _b64("Inner plain")}},
                    ]
                }
            ]
        }
        assert _extract_body(payload) == "Inner plain"

    def test_empty_payload_returns_empty_string(self):
        assert _extract_body({"body": {}}) == ""


class TestWalkParts:
    def test_yields_leaves_only(self):
        payload = {
            "parts": [
                {"mimeType": "multipart/alternative", "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("a")}},
                    {"mimeType": "text/html", "body": {"data": _b64("b")}},
                ]},
                {"mimeType": "text/plain", "body": {"data": _b64("c")}},
            ]
        }
        mimes = [p["mimeType"] for p in _walk_parts(payload)]
        assert mimes == ["text/plain", "text/html", "text/plain"]


# --- Matching -------------------------------------------------------------


class TestMatchApplication:
    @pytest.fixture
    def apps(self):
        return [
            {"company": "Tines", "title": "Software Engineer (AI)"},
            {"company": "Bending Spoons", "title": "Grad AI"},
            {"company": "Induct", "title": "Backend / Platform Engineer"},
        ]

    def _msg(self, from_addr="noreply@example.com"):
        return EmailMessage(
            account="a@gmail.com", msg_id="m1", message_id_header="<m1@x>",
            thread_id="t1", from_addr=from_addr, from_name="",
            to_addr="a@gmail.com", subject="", body="",
            date=datetime.now(timezone.utc),
        )

    def test_exact_company_match(self, apps):
        cls = Classification("rejection", "Tines", "", 0.9, "")
        assert match_application(apps, cls, self._msg())["company"] == "Tines"

    def test_normalized_legal_suffix(self, apps):
        cls = Classification("rejection", "Tines Ltd.", "", 0.9, "")
        assert match_application(apps, cls, self._msg())["company"] == "Tines"

    def test_substring_match(self, apps):
        cls = Classification("rejection", "Bending", "", 0.7, "")
        assert match_application(apps, cls, self._msg())["company"] == "Bending Spoons"

    def test_domain_fallback_when_company_blank(self, apps):
        cls = Classification("rejection", "", "", 0.5, "")
        msg = self._msg(from_addr="noreply@induct.io")
        assert match_application(apps, cls, msg)["company"] == "Induct"

    def test_no_match_returns_none(self, apps):
        cls = Classification("rejection", "Nonexistent Co", "", 0.9, "")
        assert match_application(apps, cls, self._msg()) is None


# --- Apply updates --------------------------------------------------------


class TestUpdateApplications:
    def _setup(self, prev_status="submitted"):
        app = {
            "company": "Tines",
            "title": "Software Engineer (AI)",
            "status": prev_status,
        }
        msg = EmailMessage(
            account="a@gmail.com", msg_id="m1", message_id_header="<m1@x>",
            thread_id="t1", from_addr="noreply@tines.com", from_name="",
            to_addr="a@gmail.com", subject="Update on your application",
            body="Unfortunately we won't be moving forward.",
            date=datetime.now(timezone.utc),
        )
        return app, msg

    def test_rejection_writes_history_and_status(self):
        app, msg = self._setup()
        cls = Classification("rejection", "Tines", "", 0.92, "explicit no")
        events = update_applications([(msg, cls, app)], dry_run=False)
        assert len(events) == 1
        assert app["status"] == "rejection"
        assert app["status_history"][-1]["status"] == "rejection"
        assert app["status_history"][-1]["source"] == "gmail"
        assert app["status_history"][-1]["source_email_id"] == "m1"
        assert "rejection" in app["dates"]

    def test_interview_writes_history_and_status(self):
        app, msg = self._setup()
        cls = Classification("interview_invite", "Tines", "", 0.97, "scheduling")
        events = update_applications([(msg, cls, app)], dry_run=False)
        assert events[0]["new_status"] == "interview"
        assert app["status"] == "interview"

    def test_idempotent_same_status_no_event(self):
        app, msg = self._setup(prev_status="rejection")
        cls = Classification("rejection", "Tines", "", 0.9, "")
        events = update_applications([(msg, cls, app)], dry_run=False)
        assert events == []
        assert "status_history" not in app  # nothing appended

    def test_ack_category_is_no_op(self):
        app, msg = self._setup()
        cls = Classification("ack", "Tines", "", 0.95, "received")
        events = update_applications([(msg, cls, app)], dry_run=False)
        assert events == []
        assert app["status"] == "submitted"

    def test_other_category_is_no_op(self):
        app, msg = self._setup()
        cls = Classification("other", "Tines", "", 0.4, "newsletter")
        events = update_applications([(msg, cls, app)], dry_run=False)
        assert events == []

    def test_dry_run_does_not_mutate(self):
        app, msg = self._setup()
        cls = Classification("rejection", "Tines", "", 0.92, "")
        events = update_applications([(msg, cls, app)], dry_run=True)
        assert len(events) == 1  # event still surfaced for reporting
        assert app["status"] == "submitted"  # but app NOT mutated
        assert "status_history" not in app


# --- Classifier (mocked) --------------------------------------------------


class TestClassifyMocked:
    def _msg(self, subject, body, from_addr="noreply@x.com"):
        return EmailMessage(
            account="a@gmail.com", msg_id="m1", message_id_header="<m1@x>",
            thread_id="t1", from_addr=from_addr, from_name="",
            to_addr="a@gmail.com", subject=subject, body=body,
            date=datetime.now(timezone.utc),
        )

    def test_classifier_returns_canned_rejection(self, monkeypatch):
        canned = {
            "category": "rejection",
            "company": "Induct",
            "role": "Backend Engineer",
            "confidence": 0.96,
            "rationale": "Explicit 'will not be moving forward'",
        }
        monkeypatch.setattr(
            "jobpilot.llm._call_claude",
            lambda prompt, **kw: json.dumps(canned),
        )
        msg = self._msg(
            subject="Application status update",
            body="Thank you for your interest. Unfortunately we will not "
                 "be moving forward with your application. Regards, Induct",
        )
        cls = classify(msg)
        assert cls.category == "rejection"
        assert cls.company_guess == "Induct"
        assert cls.confidence == pytest.approx(0.96)

    def test_classifier_handles_markdown_fenced_response(self, monkeypatch):
        canned = (
            "```json\n"
            '{"category":"interview_invite","company":"Tines","role":"SWE AI",'
            '"confidence":0.9,"rationale":"Scheduling a call"}\n'
            "```"
        )
        monkeypatch.setattr("jobpilot.llm._call_claude", lambda p, **kw: canned)
        msg = self._msg("Next steps", "Can we schedule a 30-min chat next week?")
        cls = classify(msg)
        assert cls.category == "interview_invite"
        assert cls.role_guess == "SWE AI"


# --- Phase 4.6: non-interactive auth handling -----------------------------


class TestNonInteractiveAuth:
    """Verifies the launchd-friendly auth failure path doesn't block on browser."""

    def _account(self):
        return {"email": "test@example.com", "token": "token_test.json"}

    def test_auth_expired_carries_account_email(self):
        exc = AuthExpired("foo@gmail.com")
        assert exc.account_email == "foo@gmail.com"
        assert "foo@gmail.com" in str(exc)

    def test_build_service_non_interactive_raises_when_no_token(self, monkeypatch, tmp_path):
        """No token file + non_interactive=True → AuthExpired, no browser."""
        import jobpilot.inbox_sync as mod

        monkeypatch.setattr(mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", tmp_path / "credentials.json")
        # Sentinel guard: any attempt to start the local OAuth server should fail loud.
        called = {"flow": False}

        class _NoServerFlow:
            @staticmethod
            def from_client_secrets_file(*a, **kw):
                called["flow"] = True
                raise AssertionError("non_interactive should never reach run_local_server")

        # Need to patch the import target inside _build_service.
        import sys as _sys
        fake_module = type(_sys)("google_auth_oauthlib.flow")
        fake_module.InstalledAppFlow = _NoServerFlow
        monkeypatch.setitem(_sys.modules, "google_auth_oauthlib.flow", fake_module)

        with pytest.raises(AuthExpired) as exc_info:
            mod._build_service(self._account(), non_interactive=True)
        assert exc_info.value.account_email == "test@example.com"
        assert called["flow"] is False

    def test_push_auth_expired_alert_calls_send_telegram(self, monkeypatch):
        sent = {}

        def fake_send(text, parse_mode=None, **kw):
            sent["text"] = text
            sent["parse_mode"] = parse_mode
            return True

        monkeypatch.setattr("jobpilot.notify.send_telegram", fake_send)
        ok = push_auth_expired_alert(["sophieineu@gmail.com", "second@gmail.com"])
        assert ok is True
        assert "sophieineu@gmail.com" in sent["text"]
        assert "second@gmail.com" in sent["text"]
        assert "OAuth refresh failed" in sent["text"]
        assert sent["parse_mode"] is None

    def test_push_auth_expired_alert_no_op_on_empty_list(self, monkeypatch):
        sent = {"called": False}

        def fake_send(text, parse_mode=None, **kw):
            sent["called"] = True
            return True

        monkeypatch.setattr("jobpilot.notify.send_telegram", fake_send)
        ok = push_auth_expired_alert([])
        assert ok is False
        assert sent["called"] is False


# --- Phase 4.5: bootstrap from unmatched acks -----------------------------


class TestBootstrapApplications:
    def _msg(self, msg_id="m1", from_addr="noreply@anthropic.com", subject="Thanks"):
        return EmailMessage(
            account="a@gmail.com", msg_id=msg_id,
            message_id_header=f"<{msg_id}@x>", thread_id="t1",
            from_addr=from_addr, from_name="",
            to_addr="a@gmail.com", subject=subject, body="",
            date=datetime.now(timezone.utc),
        )

    def test_creates_row_for_unmatched_ack(self):
        apps: list[dict] = []
        msg = self._msg(msg_id="m1", subject="Thank you for applying to Anthropic")
        cls = Classification("ack", "Anthropic", "Software Engineer", 0.96, "automated receipt")
        events = bootstrap_applications([(msg, cls)], apps, dry_run=False)
        assert len(events) == 1
        assert len(apps) == 1
        new_app = apps[0]
        assert new_app["company"] == "Anthropic"
        assert new_app["title"] == "Software Engineer"
        assert new_app["status"] == "submitted"
        assert new_app["source"] == "inbox_sync_bootstrap"
        assert new_app["job_id"] == "inbox_m1"
        assert new_app["status_history"][0]["source_email_id"] == "m1"
        assert events[0]["bootstrap"] is True

    def test_empty_role_gets_placeholder_title(self):
        apps: list[dict] = []
        msg = self._msg()
        cls = Classification("ack", "Klaviyo", "", 0.92, "")
        bootstrap_applications([(msg, cls)], apps, dry_run=False)
        assert apps[0]["title"] == "(auto-detected, role unknown)"

    def test_dedup_within_run_same_company(self):
        apps: list[dict] = []
        m1 = self._msg(msg_id="m1")
        m2 = self._msg(msg_id="m2")
        cls = Classification("ack", "Anthropic", "", 0.95, "")
        events = bootstrap_applications([(m1, cls), (m2, cls)], apps, dry_run=False)
        assert len(events) == 1
        assert len(apps) == 1

    def test_skips_if_company_already_in_apps(self):
        apps = [{"company": "Anthropic Inc.", "title": "existing", "status": "submitted"}]
        msg = self._msg()
        cls = Classification("ack", "Anthropic", "", 0.95, "")
        events = bootstrap_applications([(msg, cls)], apps, dry_run=False)
        assert len(events) == 0
        assert len(apps) == 1  # unchanged

    def test_skips_non_ack_categories(self):
        apps: list[dict] = []
        msg = self._msg()
        for cat in ("rejection", "interview_invite", "info_request", "other"):
            cls = Classification(cat, "F5", "", 0.95, "")
            events = bootstrap_applications([(msg, cls)], apps, dry_run=False)
            assert events == []
        assert apps == []

    def test_skips_empty_company_guess(self):
        apps: list[dict] = []
        msg = self._msg()
        cls = Classification("ack", "", "Engineer", 0.95, "")
        events = bootstrap_applications([(msg, cls)], apps, dry_run=False)
        assert events == []
        assert apps == []

    def test_dry_run_does_not_mutate(self):
        apps: list[dict] = []
        msg = self._msg()
        cls = Classification("ack", "Anthropic", "", 0.95, "")
        events = bootstrap_applications([(msg, cls)], apps, dry_run=True)
        assert len(events) == 1  # event still surfaced
        assert apps == []  # apps NOT appended

    def test_format_event_bootstrap_variant(self):
        msg = self._msg(subject="Thank you for applying to Klaviyo")
        cls = Classification("ack", "Klaviyo", "Backend Engineer", 0.94, "automated receipt")
        event = {
            "msg": msg, "cls": cls,
            "app": {"company": "Klaviyo", "title": "Backend Engineer"},
            "prev_status": None, "new_status": "submitted", "bootstrap": True,
        }
        text = _format_event(event)
        assert "NEW APPLICATION" in text
        assert "auto-detected" in text
        assert "Klaviyo" in text
        assert "Backend Engineer" in text


# --- Event formatting -----------------------------------------------------


class TestFormatEvent:
    def test_rejection_format(self):
        msg = EmailMessage(
            account="a@gmail.com", msg_id="m1", message_id_header="<m1>",
            thread_id="t1", from_addr="noreply@induct.io", from_name="Induct",
            to_addr="a@gmail.com", subject="Re: your application",
            body="...", date=datetime.now(timezone.utc),
        )
        cls = Classification("rejection", "Induct", "Backend", 0.94, "Won't move forward")
        event = {
            "msg": msg, "cls": cls,
            "app": {"company": "Induct", "title": "Backend / Platform Engineer"},
            "prev_status": "submitted", "new_status": "rejection",
        }
        text = _format_event(event)
        assert "REJECTION" in text
        assert "Induct" in text
        assert "Backend / Platform Engineer" in text
        assert "Won't move forward" in text
        assert "0.94" in text
