"""Tests for the digest JD-based seniority gate (cli._classify_seniority + digest).

The gate reads each candidate's JD (fetching it for LinkedIn jobs that ship
without one) and drops mid/senior, keeping graduate/junior. These tests stub the
two LLM calls (classify_role_level + fetch_full_jd) so nothing hits the network.
"""
import argparse
import json
from datetime import date

import pytest

from jobpilot import cli


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _args(**kw):
    base = dict(reset=False, send=False, max_age_days=7, limit=10,
                dry_run=True, no_jd_filter=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _write(proj, name, obj):
    (proj / "data" / name).write_text(json.dumps(obj), encoding="utf-8")


def _pipeline(proj, jobs):
    today = date.today().isoformat()
    for j in jobs:
        j.setdefault("posted_at", today)
        j.setdefault("url", "https://www.linkedin.com/jobs/view/x")
    _write(proj, "pipeline_jobs.json", jobs)
    _write(proj, "applications.json", [])
    _write(proj, "skipped.json", [])


def _stub(monkeypatch, levels, classify_track=None, fetch_track=None):
    """Stub the two LLM calls. ``levels`` maps job title -> seniority level."""
    import jobpilot.llm as llm

    def fake_classify(job):
        if classify_track is not None:
            classify_track.append(job.get("id"))
        return levels.get(job.get("title"), "junior")

    def fake_fetch(url):
        if fetch_track is not None:
            fetch_track.append(url)
        return "responsibilities " * 50  # > _JD_MIN_LEN

    monkeypatch.setattr(llm, "classify_role_level", fake_classify)
    monkeypatch.setattr(llm, "fetch_full_jd", fake_fetch)


# ---------------------------------------------------------------------------
# _classify_seniority unit tests
# ---------------------------------------------------------------------------

def test_classify_uses_cache_without_calling_llm(proj, monkeypatch):
    track = []
    _stub(monkeypatch, {}, classify_track=track)
    cache = {"j1": "senior"}
    level = cli._classify_seniority({"id": "j1", "title": "Engineer"}, cache)
    assert level == "senior"
    assert track == []  # cache hit → no classify call


def test_classify_fetches_when_description_thin(proj, monkeypatch):
    fetches = []
    _stub(monkeypatch, {"X": "junior"}, fetch_track=fetches)
    cache = {}
    cli._classify_seniority(
        {"id": "j1", "title": "X", "description": "", "url": "http://u"}, cache)
    assert fetches == ["http://u"]  # thin desc → fetched
    assert cache["j1"] == "junior"  # verdict cached


def test_classify_skips_fetch_when_description_rich(proj, monkeypatch):
    fetches = []
    _stub(monkeypatch, {"X": "mid"}, fetch_track=fetches)
    cache = {}
    cli._classify_seniority(
        {"id": "j1", "title": "X", "description": "y" * 400, "url": "http://u"},
        cache)
    assert fetches == []  # rich desc → no fetch


def test_classify_fails_open_on_exception(proj, monkeypatch):
    import jobpilot.llm as llm
    monkeypatch.setattr(llm, "fetch_full_jd", lambda url: "x" * 400)

    def boom(job):
        raise RuntimeError("claude down")
    monkeypatch.setattr(llm, "classify_role_level", boom)

    cache = {}
    level = cli._classify_seniority({"id": "j1", "title": "X"}, cache)
    assert level is None          # signals fail-open to caller
    assert "j1" not in cache      # failures are NOT cached (retry next run)


# ---------------------------------------------------------------------------
# digest() integration tests
# ---------------------------------------------------------------------------

def test_digest_drops_mid_senior_keeps_grad_junior(proj, monkeypatch, capsys):
    _pipeline(proj, [
        {"id": "g", "company": "GradCo", "title": "Graduate Software Engineer"},
        {"id": "j", "company": "JunCo", "title": "Junior Engineer"},
        {"id": "m", "company": "MidCo", "title": "Backend Engineer"},
        {"id": "s", "company": "SenCo", "title": "Platform Engineer"},
    ])
    _stub(monkeypatch, {
        "Graduate Software Engineer": "graduate",
        "Junior Engineer": "junior",
        "Backend Engineer": "mid",
        "Platform Engineer": "senior",
    })
    cli.digest(_args())
    out = capsys.readouterr().out
    assert "[GradCo]" in out
    assert "[JunCo]" in out
    assert "[MidCo]" not in out
    assert "[SenCo]" not in out
    assert "2 over-band by JD" in out


def test_digest_cache_hit_skips_classify(proj, monkeypatch, capsys):
    _pipeline(proj, [
        {"id": "g", "company": "GradCo", "title": "Graduate Engineer"},
    ])
    _write(proj, "seniority_cache.json", {"g": "junior"})
    track = []
    _stub(monkeypatch, {"Graduate Engineer": "junior"}, classify_track=track)
    cli.digest(_args())
    out = capsys.readouterr().out
    assert "[GradCo]" in out
    assert track == []  # cached verdict → no re-classification


def test_digest_fail_open_keeps_job(proj, monkeypatch, capsys):
    _pipeline(proj, [
        {"id": "x", "company": "FailCo", "title": "Mystery Engineer"},
    ])
    import jobpilot.llm as llm
    monkeypatch.setattr(llm, "fetch_full_jd", lambda url: "x" * 400)
    monkeypatch.setattr(
        llm, "classify_role_level",
        lambda job: (_ for _ in ()).throw(RuntimeError("down")))
    cli.digest(_args())
    out = capsys.readouterr().out
    assert "[FailCo]" in out             # kept despite classify failure
    assert "0 over-band by JD" in out


def test_no_jd_filter_bypasses_gate(proj, monkeypatch, capsys):
    _pipeline(proj, [
        {"id": "m", "company": "MidCo", "title": "Backend Engineer"},
    ])
    track = []
    _stub(monkeypatch, {"Backend Engineer": "mid"}, classify_track=track)
    cli.digest(_args(no_jd_filter=True))
    out = capsys.readouterr().out
    assert "[MidCo]" in out          # mid role kept (gate bypassed)
    assert track == []               # no classification happened
    assert "over-band by JD" not in out


def test_digest_persists_cache(proj, monkeypatch, capsys):
    _pipeline(proj, [
        {"id": "j", "company": "JunCo", "title": "Junior Engineer"},
    ])
    _stub(monkeypatch, {"Junior Engineer": "junior"})
    cli.digest(_args())
    cache = json.loads((proj / "data" / "seniority_cache.json").read_text())
    assert cache.get("j") == "junior"
