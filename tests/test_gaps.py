"""Tests for gap aggregation and dedup logic."""
import json
import tempfile
from pathlib import Path

import pytest

from jobpilot.gaps import (
    _normalize_hard_gap,
    _normalize_skill,
    is_gap_completed,
    load_gap_progress,
    save_gap_progress,
    scan_all_gaps,
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
