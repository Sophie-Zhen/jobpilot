"""Tests for jobpilot.discovery.opencli_source — mocked subprocess + budget logic."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpilot.discovery import opencli_source as mod
from jobpilot.discovery.opencli_source import (
    _normalize_linkedin_result,
    discover_broad,
    get_daily_usage,
    is_over_budget,
    linkedin_search,
)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def test_normalize_extracts_linkedin_job_id_from_url():
    raw = {
        "title": "Backend Engineer",
        "company": "Stripe",
        "location": "Dublin, Ireland",
        "url": "https://www.linkedin.com/jobs/view/4412345678?trk=...",
        "listed": "2026-05-10",
    }
    j = _normalize_linkedin_result(raw)
    assert j is not None
    assert j["id"] == "opencli_linkedin_4412345678"
    assert j["title"] == "Backend Engineer"
    assert j["company"] == "Stripe"
    assert j["source"] == "opencli:linkedin:search"
    assert j["ats_type"] == "linkedin_search"
    assert j["dublin_match"] == "dublin"
    assert j["posted_at"] == "2026-05-10"


def test_normalize_falls_back_to_slug_when_url_has_no_jobid():
    raw = {
        "title": "ML Engineer",
        "company": "Acme",
        "location": "Dublin",
        "url": "https://example.com/careers",
        "listed": "",
    }
    j = _normalize_linkedin_result(raw)
    assert j is not None
    # Stable fallback ID from company-title slug
    assert j["id"].startswith("opencli_linkedin_")
    assert "acme" in j["id"].lower()


def test_normalize_drops_non_dublin():
    raw = {
        "title": "Anything",
        "company": "Co",
        "location": "Paris, France",
        "url": "https://www.linkedin.com/jobs/view/123",
    }
    assert _normalize_linkedin_result(raw) is None


def test_normalize_drops_us_remote():
    raw = {
        "title": "Anything",
        "company": "Co",
        "location": "Remote - United States",
        "url": "https://www.linkedin.com/jobs/view/123",
    }
    assert _normalize_linkedin_result(raw) is None


# ---------------------------------------------------------------------------
# Daily budget tracking
# ---------------------------------------------------------------------------

class TestBudgetTracking:
    @pytest.fixture(autouse=True)
    def _isolated_usage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        usage_file = tmp_path / "api_usage.json"
        monkeypatch.setattr(mod, "_USAGE_PATH", usage_file)
        yield

    def test_initial_usage_is_zero(self):
        assert get_daily_usage() == 0
        assert not is_over_budget(budget=5)

    def test_record_call_increments(self):
        mod._record_call()
        mod._record_call()
        mod._record_call()
        assert get_daily_usage() == 3

    def test_over_budget_at_threshold(self):
        for _ in range(5):
            mod._record_call()
        assert is_over_budget(budget=5)
        assert not is_over_budget(budget=10)

    def test_only_today_counts(self):
        # Pre-seed yesterday's usage; should not affect today's count
        yesterday = "2026-01-01"
        mod._save_usage({yesterday: {"opencli": 100}})
        assert get_daily_usage() == 0
        mod._record_call()
        assert get_daily_usage() == 1
        # Yesterday's entry preserved
        data = mod._load_usage()
        assert data[yesterday]["opencli"] == 100


# ---------------------------------------------------------------------------
# linkedin_search — mock subprocess
# ---------------------------------------------------------------------------

class TestLinkedinSearch:
    @pytest.fixture(autouse=True)
    def _isolated_usage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        usage_file = tmp_path / "api_usage.json"
        monkeypatch.setattr(mod, "_USAGE_PATH", usage_file)
        monkeypatch.setattr(mod, "opencli_available", lambda: True)
        yield

    def test_returns_normalized_jobs_on_success(self):
        fake_output = [
            {"title": "Backend Engineer", "company": "Stripe", "location": "Dublin",
             "url": "https://www.linkedin.com/jobs/view/111", "listed": "2026-05-10"},
            {"title": "Sales Rep", "company": "X", "location": "New York",
             "url": "https://www.linkedin.com/jobs/view/222"},
        ]
        with patch.object(mod, "_run_opencli", return_value=(0, json.dumps(fake_output), "")):
            jobs, err = linkedin_search("AI Engineer Dublin", limit=10)
        assert err is None
        assert len(jobs) == 1  # NY filtered out
        assert jobs[0]["id"] == "opencli_linkedin_111"

    def test_returns_error_on_nonzero_exit(self):
        with patch.object(mod, "_run_opencli", return_value=(77, "", "AUTH_REQUIRED")):
            jobs, err = linkedin_search("test", limit=5)
        assert jobs == []
        assert err is not None
        assert "rc=77" in err

    def test_returns_error_on_bad_json(self):
        with patch.object(mod, "_run_opencli", return_value=(0, "not json{", "")):
            jobs, err = linkedin_search("test", limit=5)
        assert jobs == []
        assert err is not None
        assert "not JSON" in err

    def test_handles_envelope_object_response(self):
        envelope = {"ok": False, "error": {"message": "rate limited"}}
        with patch.object(mod, "_run_opencli", return_value=(0, json.dumps(envelope), "")):
            jobs, err = linkedin_search("test", limit=5)
        assert jobs == []
        assert err is not None
        assert "rate limited" in err

    def test_skips_if_over_budget(self):
        for _ in range(15):
            mod._record_call()
        with patch.object(mod, "_run_opencli") as mock_run:
            jobs, err = linkedin_search("test", limit=5, budget=15)
        assert jobs == []
        assert err is not None
        assert "budget exhausted" in err
        mock_run.assert_not_called()  # never invoked opencli

    def test_records_call_before_invocation(self):
        """Hung subprocess should still count — fail loud, not silently retry."""
        before = get_daily_usage()
        with patch.object(mod, "_run_opencli", return_value=(124, "", "timeout")):
            linkedin_search("test", limit=5)
        assert get_daily_usage() == before + 1


# ---------------------------------------------------------------------------
# discover_broad — dedup + budget exhaustion + jitter
# ---------------------------------------------------------------------------

class TestDiscoverBroad:
    @pytest.fixture(autouse=True)
    def _isolated_usage(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        usage_file = tmp_path / "api_usage.json"
        monkeypatch.setattr(mod, "_USAGE_PATH", usage_file)
        monkeypatch.setattr(mod, "opencli_available", lambda: True)
        # No-op sleep so tests run fast
        monkeypatch.setattr(mod.time, "sleep", lambda _: None)
        yield

    def test_dedups_same_job_across_queries(self):
        same_job = [{"title": "Engineer", "company": "Stripe", "location": "Dublin",
                     "url": "https://www.linkedin.com/jobs/view/999"}]
        with patch.object(mod, "_run_opencli", return_value=(0, json.dumps(same_job), "")):
            jobs, stats = discover_broad(["AI Engineer Dublin", "ML Engineer Dublin"], budget=5)
        assert len(jobs) == 1
        assert stats["total_jobs"] == 1
        assert stats["queries_attempted"] == 2
        assert stats["per_query"]["AI Engineer Dublin"]["count"] == 1
        assert stats["per_query"]["ML Engineer Dublin"]["new_after_dedup"] == 0

    def test_stops_when_budget_exhausted(self):
        fake = [{"title": "E", "company": "C", "location": "Dublin",
                 "url": "https://www.linkedin.com/jobs/view/1"}]
        with patch.object(mod, "_run_opencli", return_value=(0, json.dumps(fake), "")):
            jobs, stats = discover_broad(["q1", "q2", "q3", "q4", "q5"], budget=2)
        # Only 2 queries attempted before budget cap
        assert stats["queries_attempted"] == 2
        assert stats["daily_used"] == 2

    def test_returns_empty_when_opencli_unavailable(self, monkeypatch):
        monkeypatch.setattr(mod, "opencli_available", lambda: False)
        jobs, stats = discover_broad(["q1"], budget=5)
        assert jobs == []
        assert "error" in stats
