"""Tests for output-folder cleanup (cleanup.py)."""
import json

import pytest

from jobpilot import cleanup


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Temp data piles + output dir wired into the cleanup module."""
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(cleanup, "SAVED_PATH", tmp_path / "saved.json")
    monkeypatch.setattr(cleanup, "SKIPPED_PATH", tmp_path / "skipped.json")
    monkeypatch.setattr(cleanup, "APPLICATIONS_PATH", tmp_path / "applications.json")
    monkeypatch.setattr(cleanup, "_output_dir", lambda: out)
    return tmp_path, out


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_folder(out, name, job_id=None):
    d = out / name
    d.mkdir()
    (d / "cv_tech_eng.pdf").write_bytes(b"%PDF-1.5 fake")
    (d / "cover_letter_tech_eng.pdf").write_bytes(b"%PDF-1.5 fake")
    if job_id is not None:
        (d / "state_tech_eng.json").write_text(
            json.dumps({"job": {"id": job_id}}), encoding="utf-8"
        )
    return d


class TestIdSuffix:
    def test_trailing_digits(self):
        assert cleanup._id_suffix("opencli_linkedin_4405879439") == "4405879439"

    def test_hex_fallback_last8(self):
        assert cleanup._id_suffix("web_05b8614f") == "05b8614f"

    def test_empty(self):
        assert cleanup._id_suffix("") == ""


class TestSweep:
    def test_deletes_skipped_and_rejected_keeps_active(self, env):
        tmp, out = env
        _write(tmp / "saved.json", [])
        _write(tmp / "skipped.json", [{"job_id": "opencli_linkedin_111"}])
        _write(tmp / "applications.json", [
            {"job_id": "opencli_linkedin_222", "status": "rejection"},
            {"job_id": "opencli_linkedin_333", "status": "closed_before_apply"},
            {"job_id": "opencli_linkedin_444", "status": "submitted"},
            {"job_id": "opencli_linkedin_555", "status": "interview"},
        ])
        f_skip = _make_folder(out, "acme_eng_111", "opencli_linkedin_111")
        f_rej = _make_folder(out, "acme_eng_222", "opencli_linkedin_222")
        f_closed = _make_folder(out, "acme_eng_333", "opencli_linkedin_333")
        f_sub = _make_folder(out, "acme_eng_444", "opencli_linkedin_444")
        f_int = _make_folder(out, "acme_eng_555", "opencli_linkedin_555")

        res = cleanup.sweep(dry_run=False)

        assert not f_skip.exists()
        assert not f_rej.exists()
        assert not f_closed.exists()
        assert f_sub.exists()   # submitted — kept
        assert f_int.exists()   # interview — kept
        assert res["count"] == 3
        assert {d["job_id"] for d in res["deleted"]} == {
            "opencli_linkedin_111", "opencli_linkedin_222", "opencli_linkedin_333",
        }

    def test_dry_run_deletes_nothing(self, env):
        tmp, out = env
        _write(tmp / "saved.json", [])
        _write(tmp / "skipped.json", [{"job_id": "x_111"}])
        _write(tmp / "applications.json", [])
        f = _make_folder(out, "acme_111", "x_111")

        res = cleanup.sweep(dry_run=True)
        assert f.exists()  # still there
        assert res["count"] == 1
        assert res["bytes_freed"] > 0

    def test_keep_wins_when_job_in_both_piles(self, env):
        # A job both skipped AND a submitted application → must be KEPT.
        tmp, out = env
        _write(tmp / "saved.json", [])
        _write(tmp / "skipped.json", [{"job_id": "dup_111"}])
        _write(tmp / "applications.json", [{"job_id": "dup_111", "status": "submitted"}])
        f = _make_folder(out, "acme_111", "dup_111")

        res = cleanup.sweep(dry_run=False)
        assert f.exists()
        assert res["count"] == 0

    def test_legacy_folder_without_state_matched_by_suffix(self, env):
        tmp, out = env
        _write(tmp / "saved.json", [])
        _write(tmp / "skipped.json", [{"job_id": "web_05b8614f"}])
        _write(tmp / "applications.json", [])
        f = _make_folder(out, "web_05b8614f", job_id=None)  # no state json

        res = cleanup.sweep(dry_run=False)
        assert not f.exists()
        assert res["count"] == 1

    def test_saved_job_folder_preserved(self, env):
        tmp, out = env
        _write(tmp / "saved.json", [{"job_id": "save_111"}])
        _write(tmp / "skipped.json", [])
        _write(tmp / "applications.json", [])
        f = _make_folder(out, "acme_111", "save_111")
        res = cleanup.sweep(dry_run=False)
        assert f.exists()
        assert res["count"] == 0


class TestDeleteOutputFor:
    def test_deletes_matching(self, env):
        tmp, out = env
        f = _make_folder(out, "acme_eng_999", "job_999")
        freed = cleanup.delete_output_for("job_999")
        assert not f.exists()
        assert freed > 0

    def test_noop_for_keep_id(self, env):
        tmp, out = env
        f = _make_folder(out, "acme_eng_999", "job_999")
        freed = cleanup.delete_output_for("job_999", keep_ids={"job_999"})
        assert f.exists()
        assert freed == 0

    def test_noop_when_no_folder(self, env):
        assert cleanup.delete_output_for("ghost_404") == 0
