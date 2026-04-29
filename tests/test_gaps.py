"""Tests for gap aggregation and dedup logic."""
import json
import tempfile
from pathlib import Path

import pytest

from jobpilot.gaps import (
    _normalize_hard_gap,
    _normalize_skill,
    annotate_with_master_cv,
    is_gap_completed,
    load_gap_progress,
    save_gap_progress,
    scan_all_gaps,
    scan_ats_gaps,
)


def test_normalize_strips_parenthetical():
    assert _normalize_skill("Kubernetes (basic literacy)") == "kubernetes"
    assert _normalize_skill("T-SQL (explicit keyword + intermediate proficiency)") == "t-sql"
    assert _normalize_skill("Docker") == "docker"
    # "React fundamentals" maps to "react" via synonym table
    assert _normalize_skill("  React fundamentals (small demo)  ") == "react"
    # Unknown skills just get parenthetical stripped
    assert _normalize_skill("SomeNewTech (basic)") == "somenewtech"


def test_normalize_hard_gap():
    assert _normalize_hard_gap("Production React experience — hard to fake") == "production react experience"
    assert _normalize_hard_gap("5+ years ML -- cannot be manufactured") == "5+ years ml"
    assert _normalize_hard_gap("Simple gap with no delimiter") == "simple gap with no delimiter"


def test_scan_empty_dir():
    with tempfile.TemporaryDirectory() as tmp:
        result = scan_all_gaps(Path(tmp))
        assert result == {"quick_fill": [], "hard_gaps": []}


def test_scan_single_job():
    with tempfile.TemporaryDirectory() as tmp:
        work = {
            "job": {"id": "j1", "title": "ML Engineer", "company": "Acme"},
            "evaluation": {
                "gaps": {
                    "quick_fill": [
                        {"skill": "Docker", "how_to_fill": "Do tutorial", "suggested_bullet": "Built...", "reason_missing": "No Docker"},
                    ],
                    "hard_gaps": ["5+ years ML — cannot be manufactured"],
                }
            },
        }
        Path(tmp, "j1.json").write_text(json.dumps(work))
        result = scan_all_gaps(Path(tmp))

        assert len(result["quick_fill"]) == 1
        assert result["quick_fill"][0]["skill"] == "Docker"
        assert result["quick_fill"][0]["frequency"] == 1
        assert result["quick_fill"][0]["jobs"][0]["id"] == "j1"

        assert len(result["hard_gaps"]) == 1
        assert result["hard_gaps"][0]["frequency"] == 1


def test_dedup_merges_same_skill():
    with tempfile.TemporaryDirectory() as tmp:
        for i, (job_id, skill_name) in enumerate([
            ("j1", "Docker (basic containerization)"),
            ("j2", "Docker (deployment evidence)"),
            ("j3", "Docker"),
        ]):
            work = {
                "job": {"id": job_id, "title": f"Role {i}", "company": f"Co {i}"},
                "evaluation": {
                    "gaps": {
                        "quick_fill": [{"skill": skill_name, "how_to_fill": f"Guide {i}" * 10}],
                        "hard_gaps": [],
                    }
                },
            }
            Path(tmp, f"{job_id}.json").write_text(json.dumps(work))

        result = scan_all_gaps(Path(tmp))
        assert len(result["quick_fill"]) == 1
        assert result["quick_fill"][0]["frequency"] == 3
        assert result["quick_fill"][0]["normalized"] == "docker"


def test_sort_by_frequency():
    with tempfile.TemporaryDirectory() as tmp:
        # kubernetes appears in 1 job, python in 2
        for job_id, skills in [("j1", ["Kubernetes", "Python"]), ("j2", ["Python"])]:
            work = {
                "job": {"id": job_id, "title": "Role", "company": "Co"},
                "evaluation": {
                    "gaps": {
                        "quick_fill": [{"skill": s} for s in skills],
                        "hard_gaps": [],
                    }
                },
            }
            Path(tmp, f"{job_id}.json").write_text(json.dumps(work))

        result = scan_all_gaps(Path(tmp))
        assert result["quick_fill"][0]["normalized"] == "python"
        assert result["quick_fill"][0]["frequency"] == 2
        assert result["quick_fill"][1]["normalized"] == "kubernetes"
        assert result["quick_fill"][1]["frequency"] == 1


def test_progress_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        import jobpilot.gaps as gaps_mod
        original_path = gaps_mod._GAP_PROGRESS_PATH
        gaps_mod._GAP_PROGRESS_PATH = Path(tmp) / "progress.json"
        try:
            progress = load_gap_progress()
            assert progress == {"completed": {}}

            progress["completed"]["docker"] = {"date": "2026-04-21", "story_id": "s1"}
            save_gap_progress(progress)

            reloaded = load_gap_progress()
            assert reloaded["completed"]["docker"]["date"] == "2026-04-21"
            assert is_gap_completed(reloaded, "docker") is True
            assert is_gap_completed(reloaded, "kubernetes") is False
        finally:
            gaps_mod._GAP_PROGRESS_PATH = original_path


def test_scan_skips_malformed_files():
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "bad.json").write_text("not json")
        Path(tmp, "no_eval.json").write_text(json.dumps({"job": {}}))
        result = scan_all_gaps(Path(tmp))
        assert result == {"quick_fill": [], "hard_gaps": []}


# ---------------------------------------------------------------------------
# ATS-aware aggregation + master_cv annotation (new objective signal)
# ---------------------------------------------------------------------------

class TestAnnotateWithMasterCV:
    MASTER = (
        "Senior ML engineer with Python, FastAPI, and PyTorch experience. "
        "Built RAG pipelines with LangGraph. Familiar with Docker and AWS. "
        "Led automated testing efforts."
    )

    def test_keyword_present_returns_in_master(self):
        assert annotate_with_master_cv("Python", self.MASTER) == "in_master"
        assert annotate_with_master_cv("PyTorch", self.MASTER) == "in_master"
        assert annotate_with_master_cv("FastAPI", self.MASTER) == "in_master"

    def test_synonym_resolves_to_in_master(self):
        # "AWS" → canonical "amazon web services" (alias group includes "aws")
        assert annotate_with_master_cv("AWS", self.MASTER) == "in_master"

    def test_partial_token_returns_partial(self):
        # "Unit Testing" — neither full phrase nor stem "test" is in master,
        # but "testing" IS in master. Token "testing" matches → partial.
        assert annotate_with_master_cv("Unit Testing", self.MASTER) == "partial"

    def test_completely_absent_returns_absent(self):
        assert annotate_with_master_cv("Kubernetes", self.MASTER) == "absent"
        assert annotate_with_master_cv("FHIR", self.MASTER) == "absent"

    def test_empty_master_returns_absent(self):
        assert annotate_with_master_cv("Python", "") == "absent"


class TestScanATSGaps:
    def _write_work_file(self, tmp: str, job_id: str, missing_must: list[str], missing_nice: list[str] = None) -> None:
        work = {
            "job": {"id": job_id, "title": f"Role {job_id}", "company": "Co"},
            "evaluation": {
                "ats_score": {
                    "overall": 0.4,
                    "coverage": {
                        "missing_must": missing_must,
                        "missing_nice": missing_nice or [],
                        "matched_must": [],
                        "matched_nice": [],
                    },
                }
            },
        }
        Path(tmp, f"{job_id}.json").write_text(json.dumps(work))

    def test_empty_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = scan_ats_gaps(Path(tmp), master_cv_text="")
            assert result == {"missing_must": [], "missing_nice": []}

    def test_skips_files_without_ats_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            # File with only old-style gaps (no ats_score) — should be skipped
            Path(tmp, "old.json").write_text(json.dumps({
                "job": {"id": "old"},
                "evaluation": {"gaps": {"quick_fill": [{"skill": "Docker"}]}},
            }))
            result = scan_ats_gaps(Path(tmp), master_cv_text="")
            assert result == {"missing_must": [], "missing_nice": []}

    def test_aggregates_across_jobs_sorted_by_freq(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_work_file(tmp, "j1", ["Kubernetes", "FHIR"])
            self._write_work_file(tmp, "j2", ["Kubernetes", "Java"])
            self._write_work_file(tmp, "j3", ["Kubernetes"])
            result = scan_ats_gaps(Path(tmp), master_cv_text="")
            must = result["missing_must"]
            assert must[0]["normalized"] == "kubernetes"
            assert must[0]["frequency"] == 3
            assert len(must[0]["jobs"]) == 3
            # Java and FHIR each appear once
            others = {e["normalized"] for e in must[1:]}
            assert others == {"java", "fhir"}

    def test_annotates_against_master_cv(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_work_file(tmp, "j1", ["Python", "Kubernetes"])
            master = "Python engineer with FastAPI experience."
            result = scan_ats_gaps(Path(tmp), master_cv_text=master)
            ann = {e["normalized"]: e["annotation"] for e in result["missing_must"]}
            assert ann["python"] == "in_master"
            assert ann["kubernetes"] == "absent"

    def test_separates_must_and_nice(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_work_file(tmp, "j1", ["Java"], ["Spark"])
            result = scan_ats_gaps(Path(tmp), master_cv_text="")
            assert len(result["missing_must"]) == 1
            assert result["missing_must"][0]["normalized"] == "java"
            assert len(result["missing_nice"]) == 1
            assert result["missing_nice"][0]["normalized"] == "spark"
