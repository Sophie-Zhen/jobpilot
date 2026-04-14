import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


class TestDedupFilter:
    def test_seen_job_filtered(self, tmp_path):
        seen_path = tmp_path / "seen_jobs.json"
        seen_path.write_text(json.dumps({
            "job_123": {"first_seen": date.today().isoformat(), "source": "test", "title": "Old Job"}
        }))

        with patch("jobpilot.job_sources._SEEN_JOBS_PATH", seen_path):
            from jobpilot.job_sources import _apply_profile_filters

            jobs = [
                {"id": "job_123", "title": "Old Job", "location": "", "description": ""},
                {"id": "job_456", "title": "New Job", "location": "", "description": ""},
            ]
            result = _apply_profile_filters(jobs, {}, limit=10)
            ids = [j["id"] for j in result]
            assert "job_123" not in ids
            assert "job_456" in ids

    def test_new_job_passes(self, tmp_path):
        seen_path = tmp_path / "seen_jobs.json"
        seen_path.write_text(json.dumps({}))

        with patch("jobpilot.job_sources._SEEN_JOBS_PATH", seen_path):
            from jobpilot.job_sources import _apply_profile_filters

            jobs = [{"id": "job_new", "title": "Fresh", "location": "", "description": ""}]
            result = _apply_profile_filters(jobs, {}, limit=10)
            assert len(result) == 1

    def test_90_day_pruning(self, tmp_path):
        seen_path = tmp_path / "seen_jobs.json"
        old_date = (date.today() - timedelta(days=100)).isoformat()
        recent_date = (date.today() - timedelta(days=10)).isoformat()
        seen_path.write_text(json.dumps({
            "old_job": {"first_seen": old_date, "source": "test", "title": "Old"},
            "recent_job": {"first_seen": recent_date, "source": "test", "title": "Recent"},
        }))

        with patch("jobpilot.job_sources._SEEN_JOBS_PATH", seen_path):
            from jobpilot.job_sources import _load_seen_jobs

            seen = _load_seen_jobs()
            assert "old_job" not in seen
            assert "recent_job" in seen

    def test_corrupted_seen_jobs(self, tmp_path):
        seen_path = tmp_path / "seen_jobs.json"
        seen_path.write_text("not valid json!!!")

        with patch("jobpilot.job_sources._SEEN_JOBS_PATH", seen_path):
            from jobpilot.job_sources import _load_seen_jobs

            seen = _load_seen_jobs()
            assert seen == {}

    def test_missing_seen_jobs_file(self, tmp_path):
        seen_path = tmp_path / "nonexistent.json"

        with patch("jobpilot.job_sources._SEEN_JOBS_PATH", seen_path):
            from jobpilot.job_sources import _load_seen_jobs

            seen = _load_seen_jobs()
            assert seen == {}
