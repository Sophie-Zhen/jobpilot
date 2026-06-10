"""Tests for the digest seniority (over-band) filter."""
import argparse
import json
from datetime import date

import pytest

from jobpilot.cli import _is_over_band, digest


class TestIsOverBand:
    @pytest.mark.parametrize("title", [
        "Senior Software Engineer",
        "Sr Machine Learning Engineer",
        "Staff Software Engineer – Machine Learning",
        "Principal Software Engineer",
        "AI Technical Lead",
        "Lead Data Scientist (Brightflag)",
        "AI Technology Manager - ML & Automation",
        "Director of Engineering",
        "Head of Data Science",
        "VP, Engineering",
        "Applied AI Architect",
        "Distinguished Engineer",
    ])
    def test_over_band_titles_excluded(self, title):
        assert _is_over_band(title) is True

    @pytest.mark.parametrize("title", [
        "Software Engineer",
        "AI Engineer",
        "Machine Learning Engineer",
        "Graduate Software Engineer",
        "Junior Python Engineer (Test Automation)",
        "Data Scientist",
        "Software Engineer III, Global Network",
        "Full Stack Engineer, Support Experience",
    ])
    def test_in_band_titles_kept(self, title):
        assert _is_over_band(title) is False


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _args(**kw):
    base = dict(reset=False, send=False, max_age_days=7, limit=10, dry_run=True)
    base.update(kw)
    return argparse.Namespace(**base)


def _write(proj, name, obj):
    (proj / "data" / name).write_text(json.dumps(obj), encoding="utf-8")


def test_digest_drops_over_band_keeps_in_band(proj, capsys):
    today = date.today().isoformat()
    _write(proj, "pipeline_jobs.json", [
        {"id": "p1", "company": "Red Hat", "title": "Principal Software Engineer",
         "posted_at": today, "url": "u"},
        {"id": "p2", "company": "Arm", "title": "Staff Software Engineer – Machine Learning",
         "posted_at": today, "url": "u"},
        {"id": "p3", "company": "Marsh", "title": "AI Engineer", "posted_at": today, "url": "u"},
    ])
    _write(proj, "applications.json", [])
    _write(proj, "skipped.json", [])

    digest(_args())
    out = capsys.readouterr().out
    assert "[Marsh] AI Engineer" in out
    assert "Principal" not in out
    assert "Staff" not in out
    assert "2 over-band" in out
