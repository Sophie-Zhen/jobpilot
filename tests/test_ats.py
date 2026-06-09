"""Tests for the ATS simulator. LLM paths are mocked — these must run offline."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from jobpilot import ats
from jobpilot.ats import (
    ATSScore,
    CoverageResult,
    JDRequirements,
    ats_score,
    check_pdf_parseability,
    cv_data_to_text,
    extract_jd_requirements,
    format_audit,
    keyword_coverage,
    normalize,
)


# ---------------------------------------------------------------------------
# Normalization + synonym resolution
# ---------------------------------------------------------------------------
class TestNormalize:
    def test_lowercase(self):
        assert normalize("Python") == "python"

    def test_synonym_k8s(self):
        assert normalize("k8s") == "kubernetes"

    def test_synonym_aws(self):
        assert normalize("AWS") == "amazon web services"

    def test_synonym_llm(self):
        assert normalize("LLMs") == "large language model"

    def test_strips_punctuation(self):
        assert normalize("Python,") == "python"

    def test_preserves_version_markers(self):
        # Plus, dot, hash, slash, hyphen are legal in tech names (C++, C#, Node.js)
        assert "c++" in normalize("C++")

    def test_unknown_stays_normalized(self):
        assert normalize("WeirdToolX") == "weirdtoolx"


# ---------------------------------------------------------------------------
# CV dict → text
# ---------------------------------------------------------------------------
class TestCVDataToText:
    def test_includes_summary(self):
        cv = {"summary": "Python engineer.", "experience": [], "skills": {}}
        assert "Python engineer." in cv_data_to_text(cv)

    def test_flattens_skills_dict(self):
        cv = {
            "summary": "",
            "experience": [],
            "skills": {"Languages": ["Python", "Go"], "Tools": ["Docker"]},
        }
        text = cv_data_to_text(cv)
        assert "Python" in text and "Go" in text and "Docker" in text

    def test_flattens_experience_bullets(self):
        cv = {
            "summary": "",
            "experience": [
                {
                    "title": "SWE",
                    "company": "Acme",
                    "bullets": ["Built RAG pipeline.", "Shipped LLM agent."],
                }
            ],
            "skills": {},
        }
        text = cv_data_to_text(cv)
        assert "RAG pipeline" in text and "LLM agent" in text
        assert "Acme" in text

    def test_accepts_skills_as_list(self):
        cv = {"summary": "", "experience": [], "skills": ["Python", "SQL"]}
        text = cv_data_to_text(cv)
        assert "Python" in text and "SQL" in text


# ---------------------------------------------------------------------------
# Keyword coverage scoring
# ---------------------------------------------------------------------------
class TestKeywordCoverage:
    def test_perfect_match(self):
        text = "Python PyTorch Kubernetes"
        reqs = JDRequirements(must_have=["Python", "PyTorch"], nice_to_have=["Kubernetes"])
        cov = keyword_coverage(text, reqs)
        assert cov.score == 1.0
        assert cov.missing_must == []

    def test_no_matches(self):
        reqs = JDRequirements(must_have=["Java"], nice_to_have=["Spring"])
        cov = keyword_coverage("Python engineer", reqs)
        assert cov.score == 0.0

    def test_must_weighted_2x(self):
        # All must matched, no nice matched → (2*1 + 0)/3 = 0.667
        reqs = JDRequirements(must_have=["Python"], nice_to_have=["Rust"])
        cov = keyword_coverage("Python engineer", reqs)
        assert cov.score == pytest.approx(0.667, abs=0.001)

    def test_synonym_match(self):
        # "k8s" in JD should match "Kubernetes" in CV (and vice versa)
        reqs = JDRequirements(must_have=["k8s"], nice_to_have=[])
        cov = keyword_coverage("Kubernetes cluster experience", reqs)
        assert cov.matched_must == ["k8s"]

    def test_empty_requirements(self):
        reqs = JDRequirements()
        cov = keyword_coverage("anything", reqs)
        # No requirements → perfect score by definition
        assert cov.score == 1.0

    def test_word_boundary_matching(self):
        # "Java" must not match "JavaScript"
        reqs = JDRequirements(must_have=["Java"], nice_to_have=[])
        cov = keyword_coverage("JavaScript engineer", reqs)
        assert cov.missing_must == ["Java"]


# ---------------------------------------------------------------------------
# JD requirement extraction (regex-only path, LLM disabled — keeps tests fast)
# ---------------------------------------------------------------------------
class TestExtractJDRequirements:
    def test_empty_jd(self):
        reqs = extract_jd_requirements("", use_llm=False, use_cache=False)
        assert reqs.must_have == [] and reqs.nice_to_have == []

    def test_too_short(self):
        reqs = extract_jd_requirements("short", use_llm=False, use_cache=False)
        assert reqs.must_have == [] and reqs.nice_to_have == []

    def test_regex_surfaces_capitalized_tech(self):
        jd = (
            "We need an engineer with strong Python skills. "
            "Experience with PyTorch and Kubernetes is required. "
            "Familiarity with FastAPI is a plus."
        )
        reqs = extract_jd_requirements(jd, use_llm=False, use_cache=False)
        # Regex-only puts everything in nice_to_have (LLM pass would split it)
        combined = {s.lower() for s in reqs.nice_to_have}
        assert "python" in combined
        assert "pytorch" in combined


# ---------------------------------------------------------------------------
# PDF parseability + format audit
# ---------------------------------------------------------------------------
def _render_real_cv_pdf(tmp_path: Path) -> Path:
    """Render a real CV PDF using the project's LaTeX template. Skips test if
    pdflatex is unavailable."""
    pytest.importorskip("jinja2")
    from jobpilot.renderer import render_cv
    import shutil

    if shutil.which("pdflatex") is None:
        pytest.skip("pdflatex not installed")

    data = {
        "name": "Test User",
        "email": "test@example.com",
        "phone": "+353 87 000 0000",
        "location": "Dublin, Ireland",
        "summary": "Python engineer with PyTorch and Kubernetes experience.",
        "experience": [
            {
                "title": "Senior Engineer",
                "company": "Acme",
                "dates": "2024",
                "location": "Dublin",
                "bullets": ["Built RAG pipelines.", "Shipped LLM agents."],
            }
        ],
        "education": [
            {
                "degree": "MSc CS",
                "institution": "DCU",
                "dates": "2025",
                "location": "Dublin",
                "details": [],
            }
        ],
        "skills": {
            "Languages": ["Python", "TypeScript"],
            "ML & AI": ["PyTorch", "LangGraph"],
            "Tools & Frameworks": ["Kubernetes", "Docker"],
            "Other": [],
        },
        "projects": [],
        "awards": [],
    }
    return render_cv(data, tmp_path / "cv.pdf")


class TestPDFParseability:
    def test_real_cv_is_parseable(self, tmp_path):
        pdf = _render_real_cv_pdf(tmp_path)
        result = check_pdf_parseability(pdf)
        assert result.parseable is True
        assert result.has_email is True
        assert result.has_phone is True
        assert "experience" in result.found_sections
        assert "education" in result.found_sections


class TestFormatAudit:
    def test_real_cv_has_no_critical_issues(self, tmp_path):
        pdf = _render_real_cv_pdf(tmp_path)
        issues = format_audit(pdf)
        critical = [i for i in issues if i.severity == "critical"]
        # The project's own LaTeX template must not trip critical ATS killers.
        assert critical == [], f"Unexpected critical issues: {critical}"


# ---------------------------------------------------------------------------
# Composite scoring
# ---------------------------------------------------------------------------
class TestATSScore:
    def test_coverage_only_path(self):
        cv = {
            "summary": "Python engineer.",
            "experience": [],
            "skills": {"Languages": ["Python"]},
        }
        # Pass pre-built requirements via the cache to avoid LLM
        jd_text = "Python " * 20  # long enough to not be skipped
        cache = ats._cache_path(jd_text)
        cache.write_text(
            json.dumps({"must_have": ["Python"], "nice_to_have": [], "source": "test"}),
            encoding="utf-8",
        )
        try:
            score = ats_score(cv_data=cv, jd_text=jd_text, use_llm=False)
            assert score.overall == 1.0
            assert score.threshold_passed is True
        finally:
            cache.unlink(missing_ok=True)

    def test_requires_cv_or_pdf(self):
        with pytest.raises(ValueError):
            ats_score(jd_text="anything")

    def test_threshold_configurable(self):
        cv = {"summary": "", "experience": [], "skills": {}}
        jd_text = "Python " * 20
        cache = ats._cache_path(jd_text)
        cache.write_text(
            json.dumps({"must_have": ["Python"], "nice_to_have": [], "source": "test"}),
            encoding="utf-8",
        )
        try:
            score = ats_score(cv_data=cv, jd_text=jd_text, use_llm=False, threshold=0.0)
            # Zero coverage but threshold 0.0 → passes
            assert score.overall == 0.0
            assert score.threshold_passed is True
        finally:
            cache.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Keyword stuffing penalty — recruiters screen tech resumes by hand.
# ---------------------------------------------------------------------------
class TestStuffingPenalty:
    def test_normal_cv_no_penalty(self):
        from jobpilot.ats import JDRequirements, keyword_stuffing_penalty
        reqs = JDRequirements(must_have=["Python", "Kubernetes"], nice_to_have=["Docker"])
        text = "Built a Python service on Kubernetes. Used Docker for local dev."
        assert keyword_stuffing_penalty(text, reqs) == 0.0

    def test_stuffed_keyword_penalized(self):
        from jobpilot.ats import JDRequirements, keyword_stuffing_penalty
        reqs = JDRequirements(must_have=["Python"], nice_to_have=[])
        # Adjacent repeats must all count (zero-width boundaries).
        assert keyword_stuffing_penalty("Python " * 8, reqs) > 0.0

    def test_penalty_is_capped(self):
        from jobpilot.ats import (
            _STUFFING_MAX_PENALTY,
            JDRequirements,
            keyword_stuffing_penalty,
        )
        reqs = JDRequirements(must_have=["Python", "Java", "Go", "Rust", "SQL"], nice_to_have=[])
        text = " ".join(kw + " " * 0 + (" " + kw) * 7 for kw in ["Python", "Java", "Go", "Rust", "SQL"])
        assert keyword_stuffing_penalty(text, reqs) == _STUFFING_MAX_PENALTY

    def test_ats_score_subtracts_stuffing(self):
        jd_text = "Python " * 20
        cache = ats._cache_path(jd_text)
        cache.write_text(
            json.dumps({"must_have": ["Python"], "nice_to_have": [], "source": "test"}),
            encoding="utf-8",
        )
        try:
            stuffed = {
                "summary": "",
                "experience": [{"title": "E", "company": "C", "bullets": ["Python " * 8]}],
                "skills": {"Languages": ["Python"]},
            }
            score = ats_score(cv_data=stuffed, jd_text=jd_text, use_llm=False)
            # Coverage alone would be 1.0; stuffing knocks it down.
            assert score.coverage.score == 1.0
            assert score.overall < 1.0
        finally:
            cache.unlink(missing_ok=True)
