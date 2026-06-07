"""Tests for the Telegram bot Phase 3 disposition logic (bot.py).

Covers the pure persistence helpers and keyboard builders. The async
callback router and the tailor-subprocess send are exercised by live
smoke-testing (they need Telegram + claude), not unit tests.
"""
import json
from datetime import date

import pytest

from jobpilot import bot


@pytest.fixture
def datadir(tmp_path, monkeypatch):
    """Point bot's data files at a temp dir so tests don't touch real data."""
    monkeypatch.setattr(bot, "_SAVED_PATH", tmp_path / "saved.json")
    monkeypatch.setattr(bot, "_SKIPPED_PATH", tmp_path / "skipped.json")
    monkeypatch.setattr(bot, "_APPLICATIONS_PATH", tmp_path / "applications.json")
    monkeypatch.setattr(bot, "_PIPELINE_PATH", tmp_path / "pipeline_jobs.json")
    return tmp_path


JOB = {
    "id": "opencli_linkedin_4405879439",
    "title": "Software Engineer",
    "company": "Cisco",
    "url": "https://www.linkedin.com/jobs/view/4405879439",
    "date_found": "2026-06-05",
}


# --- keyboard builders -----------------------------------------------------

class TestMarkup:
    def test_fresh_card_has_save_and_skip(self):
        kb = bot.build_card_markup_dict("job123")["inline_keyboard"]
        codes = [b["callback_data"] for row in kb for b in row]
        assert codes == [f"{bot.ACTION_SAVE}:job123", f"{bot.ACTION_SKIP}:job123"]

    def test_post_save_has_tailor_applied_drop(self):
        kb = bot.build_post_save_markup("job123").inline_keyboard
        codes = [b.callback_data for row in kb for b in row]
        assert codes == [
            f"{bot.ACTION_TAILOR}:job123",
            f"{bot.ACTION_APPLIED_CL}:job123",
            f"{bot.ACTION_APPLIED_NOCL}:job123",
            f"{bot.ACTION_DROP}:job123",
        ]

    def test_callback_data_under_telegram_64_byte_cap(self):
        long_id = "opencli_linkedin_4405879439"  # realistic worst case
        for row in bot.build_post_save_markup(long_id).inline_keyboard:
            for b in row:
                assert len(b.callback_data.encode("utf-8")) <= 64


# --- _record_applied -------------------------------------------------------

class TestRecordApplied:
    def test_writes_submitted_with_cover_letter(self, datadir):
        assert bot._record_applied(JOB, cover_letter_uploaded=True) == "logged"
        apps = json.loads((datadir / "applications.json").read_text())
        assert len(apps) == 1
        a = apps[0]
        assert a["job_id"] == JOB["id"]
        assert a["status"] == "submitted"
        assert a["cover_letter_uploaded"] is True
        assert a["date_discovered"] == "2026-06-05"  # from job.date_found
        assert a["date_submitted"] == date.today().isoformat()

    def test_no_cover_letter_flag_false(self, datadir):
        bot._record_applied(JOB, cover_letter_uploaded=False)
        apps = json.loads((datadir / "applications.json").read_text())
        assert apps[0]["cover_letter_uploaded"] is False

    def test_idempotent_on_job_id(self, datadir):
        assert bot._record_applied(JOB, True) == "logged"
        assert bot._record_applied(JOB, True) == "already_logged"
        apps = json.loads((datadir / "applications.json").read_text())
        assert len(apps) == 1

    def test_date_discovered_falls_back_to_today(self, datadir):
        job = {k: v for k, v in JOB.items() if k != "date_found"}
        bot._record_applied(job, True)
        apps = json.loads((datadir / "applications.json").read_text())
        assert apps[0]["date_discovered"] == date.today().isoformat()


# --- _remove_from_saved ----------------------------------------------------

class TestRemoveFromSaved:
    def test_removes_matching_job(self, datadir):
        bot._save_json_list(bot._SAVED_PATH, [
            {"job_id": "a"}, {"job_id": JOB["id"]}, {"job_id": "b"},
        ])
        bot._remove_from_saved(JOB["id"])
        remaining = json.loads((datadir / "saved.json").read_text())
        assert [s["job_id"] for s in remaining] == ["a", "b"]

    def test_noop_when_absent(self, datadir):
        bot._save_json_list(bot._SAVED_PATH, [{"job_id": "a"}])
        bot._remove_from_saved("not-there")
        remaining = json.loads((datadir / "saved.json").read_text())
        assert [s["job_id"] for s in remaining] == ["a"]


# --- _lookup_job -----------------------------------------------------------

class TestLookupJob:
    def test_prefers_pipeline(self, datadir):
        bot._save_json_list(bot._PIPELINE_PATH, [JOB])
        found = bot._lookup_job(JOB["id"])
        assert found["company"] == "Cisco"
        assert found["date_found"] == "2026-06-05"

    def test_falls_back_to_saved(self, datadir):
        bot._save_json_list(bot._PIPELINE_PATH, [])
        bot._save_json_list(bot._SAVED_PATH, [{
            "job_id": JOB["id"], "title": "Software Engineer",
            "company": "Cisco", "url": JOB["url"], "date_saved": "2026-06-05",
        }])
        found = bot._lookup_job(JOB["id"])
        assert found["id"] == JOB["id"]
        assert found["company"] == "Cisco"
        assert found["date_found"] == "2026-06-05"  # mapped from date_saved

    def test_returns_none_when_unknown(self, datadir):
        bot._save_json_list(bot._PIPELINE_PATH, [])
        assert bot._lookup_job("ghost") is None


# --- apply flow integration (helpers composed) -----------------------------

def test_apply_flow_moves_saved_to_applications(datadir):
    bot._save_json_list(bot._PIPELINE_PATH, [JOB])
    bot._record_saved(JOB)
    assert len(json.loads((datadir / "saved.json").read_text())) == 1

    # simulate the Applied +Cover tap's writes
    bot._record_applied(JOB, cover_letter_uploaded=True)
    bot._remove_from_saved(JOB["id"])

    assert json.loads((datadir / "saved.json").read_text()) == []
    apps = json.loads((datadir / "applications.json").read_text())
    assert apps[0]["job_id"] == JOB["id"]
    assert apps[0]["cover_letter_uploaded"] is True
