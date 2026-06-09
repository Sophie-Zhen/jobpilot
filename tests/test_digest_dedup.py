"""Tests for digest cross-posting dedup (cli._norm_key + digest filter)."""
import argparse
import json
from datetime import date

import pytest

from jobpilot.cli import _norm_key, digest


class TestNormKey:
    def test_collapses_same_title_diff_punctuation(self):
        a = _norm_key("EY", "AI & Data - Agentic AI Engineer - Senior Consultant")
        b = _norm_key("EY", "AI  Data   Agentic AI Engineer   Senior Consultant")
        assert a == b

    def test_case_insensitive(self):
        assert _norm_key("Marsh", "AI Engineer") == _norm_key("MARSH", "ai engineer")

    def test_distinguishes_different_titles(self):
        # Two genuinely different EY roles must NOT collapse.
        assert _norm_key("EY", "Agentic AI Engineer") != _norm_key("EY", "AI Delivery Lead")


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


def test_excludes_role_already_applied_under_different_id(proj, capsys):
    today = date.today().isoformat()
    # Same role, identical title, different posting id (the real EY case).
    _write(proj, "pipeline_jobs.json", [
        {"id": "ey_new", "company": "EY", "title": "AI & Data - Agentic AI Engineer - Senior Consultant",
         "posted_at": today, "url": "u"},
        {"id": "marsh_1", "company": "Marsh", "title": "AI Engineer", "posted_at": today, "url": "u"},
    ])
    _write(proj, "applications.json", [
        {"job_id": "ey_old", "company": "EY",
         "title": "AI & Data - Agentic AI Engineer - Senior Consultant", "status": "submitted"},
    ])
    _write(proj, "skipped.json", [])

    digest(_args())
    out = capsys.readouterr().out
    assert "[Marsh] AI Engineer" in out
    assert "Agentic AI Engineer" not in out  # EY repost suppressed by title match


def test_excludes_skipped_id(proj, capsys):
    today = date.today().isoformat()
    _write(proj, "pipeline_jobs.json", [
        {"id": "bs_1", "company": "Bending Spoons", "title": "Graduate software engineer",
         "posted_at": today, "url": "u"},
    ])
    _write(proj, "applications.json", [])
    _write(proj, "skipped.json", [
        {"job_id": "bs_1", "company": "Bending Spoons", "title": "Graduate software engineer"},
    ])

    digest(_args())
    out = capsys.readouterr().out
    assert "Nothing to digest" in out


def test_collapses_same_role_within_run(proj, capsys):
    today = date.today().isoformat()
    _write(proj, "pipeline_jobs.json", [
        {"id": "dup_a", "company": "Docusign", "title": "Machine Learning Engineer",
         "posted_at": today, "url": "u"},
        {"id": "dup_b", "company": "Docusign", "title": "Machine Learning Engineer",
         "posted_at": today, "url": "u"},
    ])
    _write(proj, "applications.json", [])
    _write(proj, "skipped.json", [])

    digest(_args())
    out = capsys.readouterr().out
    # Two identical-role postings collapse to one card.
    assert out.count("[Docusign] Machine Learning Engineer") == 1
    assert "1 dup-of-seen" in out
