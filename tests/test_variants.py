"""Tests for L3 variant infrastructure (framing rules + page-count check)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpilot.llm import (
    FRAMING_RULES_BY_VARIANT,
    FRAMING_RULES_GRAD,
    FRAMING_RULES_REGTECH,
    FRAMING_RULES_TECH_ENG,
    TARGET_PAGES_BY_VARIANT,
    VALID_VARIANTS,
    tailor_cv,
)
from jobpilot.renderer import (
    check_page_count,
    pdf_page_info,
    render_cv,
)


class TestVariantConstants:
    def test_valid_variants_are_three(self):
        assert set(VALID_VARIANTS) == {"grad", "tech_eng", "regtech"}

    def test_framing_rules_keyed_by_variant(self):
        assert set(FRAMING_RULES_BY_VARIANT.keys()) == set(VALID_VARIANTS)

    def test_each_framing_rule_is_distinct(self):
        rules = set(FRAMING_RULES_BY_VARIANT.values())
        assert len(rules) == 3

    def test_grad_rule_emphasizes_graduate_identity(self):
        assert "graduate" in FRAMING_RULES_GRAD.lower()
        assert "msc" in FRAMING_RULES_GRAD.lower()

    def test_regtech_rule_emphasizes_domain_combination(self):
        # regtech variant explicitly flips the default by leading with domain depth
        assert "13 years" in FRAMING_RULES_REGTECH
        assert "regtech" in FRAMING_RULES_REGTECH.lower()
        assert "financial auditing" in FRAMING_RULES_REGTECH.lower()

    def test_tech_eng_rule_keeps_engineer_first(self):
        assert "engineer" in FRAMING_RULES_TECH_ENG.lower()
        # Self-learning since 2018 belongs in tech_eng framing
        assert "2018" in FRAMING_RULES_TECH_ENG

    def test_target_pages_per_variant(self):
        assert TARGET_PAGES_BY_VARIANT == {"grad": 1, "tech_eng": 2, "regtech": 2}


class TestTailorCVVariantValidation:
    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError, match="Unknown variant"):
            tailor_cv(
                job={"title": "x", "company": "y", "description": "z"},
                stories=[],
                profile={},
                variant="bogus",
            )

    def test_unknown_variant_does_not_call_llm(self):
        # Validation must happen BEFORE any LLM call
        with patch("jobpilot.llm._call_claude") as mock_call:
            with pytest.raises(ValueError):
                tailor_cv(
                    job={"title": "x", "company": "y", "description": "z"},
                    stories=[],
                    profile={},
                    variant="bogus",
                )
        mock_call.assert_not_called()


class TestTailorCVVariantWiring:
    """Variant selection should change the prompt content before LLM is called."""

    @staticmethod
    def _stub_response() -> str:
        return json.dumps(
            {
                "summary": "test",
                "include_fudan": False,
                "experience_bullet_indices": {"huawei": [0], "walkers": [0], "tax_bureau": [0]},
                "project_ids": ["jobpilot"],
                "skills": {"Languages": ["Python"], "ML & AI": [], "Tools & Frameworks": [], "Other": []},
                "include_awards": False,
            }
        )

    def test_grad_variant_forces_graduate_role_level(self):
        captured: dict[str, str] = {}

        def capture(prompt: str, timeout: int = 600, tools: list | None = None) -> str:  # noqa: ARG001
            captured["prompt"] = prompt
            return self._stub_response()

        with patch("jobpilot.llm._call_claude", side_effect=capture):
            tailor_cv(
                job={"title": "Senior ML Engineer", "company": "x", "description": "x"},
                stories=[],
                profile={},
                variant="grad",
            )

        # grad variant overrides any auto-classified role_level to 'graduate'
        assert "Graduate/Entry-level engineering" in captured["prompt"]

    def test_regtech_variant_surfaces_financial_auditing_skill(self):
        captured: dict[str, str] = {}

        def capture(prompt: str, timeout: int = 600, tools: list | None = None) -> str:  # noqa: ARG001
            captured["prompt"] = prompt
            return self._stub_response()

        with patch("jobpilot.llm._call_claude", side_effect=capture):
            tailor_cv(
                job={"title": "Compliance AI Engineer", "company": "x", "description": "x"},
                stories=[],
                profile={},
                variant="regtech",
            )

        # Regtech variant restores Financial Auditing to the visible skills list
        assert "Financial Auditing" in captured["prompt"]

    def test_tech_eng_variant_does_not_surface_financial_auditing(self):
        captured: dict[str, str] = {}

        def capture(prompt: str, timeout: int = 600, tools: list | None = None) -> str:  # noqa: ARG001
            captured["prompt"] = prompt
            return self._stub_response()

        with patch("jobpilot.llm._call_claude", side_effect=capture):
            tailor_cv(
                job={"title": "ML Engineer", "company": "x", "description": "x"},
                stories=[],
                profile={},
                variant="tech_eng",
            )

        # Tech_eng variant keeps Financial Auditing out (it was removed in L2)
        assert "Financial Auditing" not in captured["prompt"]

    def test_each_variant_injects_its_framing_rules(self):
        for variant in VALID_VARIANTS:
            captured: dict[str, str] = {}

            def capture(prompt: str, timeout: int = 600, tools: list | None = None) -> str:  # noqa: ARG001
                captured["prompt"] = prompt
                return self._stub_response()

            with patch("jobpilot.llm._call_claude", side_effect=capture):
                tailor_cv(
                    job={"title": "Engineer", "company": "x", "description": "x"},
                    stories=[],
                    profile={},
                    variant=variant,
                )

            assert FRAMING_RULES_BY_VARIANT[variant] in captured["prompt"], (
                f"variant={variant} did not inject its framing rules"
            )


class TestPageCountCheck:
    """pdf_page_info + check_page_count helpers."""

    @staticmethod
    def _minimal_cv_data() -> dict:
        return {
            "name": "Test User",
            "email": "test@example.com",
            "location": "Dublin",
            "summary": "A test summary.",
            "experience": [
                {
                    "title": "Engineer",
                    "company": "TestCo",
                    "dates": "2024",
                    "bullets": ["Did things", "Built stuff"],
                }
            ],
            "skills": ["python", "sql"],
        }

    def test_pdf_page_info_returns_at_least_one_page(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = render_cv(self._minimal_cv_data(), Path(d) / "cv.pdf")
            info = pdf_page_info(pdf)
        assert info["page_count"] >= 1
        assert len(info["page_text_lengths"]) == info["page_count"]
        assert info["last_page_fill_ratio"] is not None

    def test_check_page_count_passes_when_target_met(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = render_cv(self._minimal_cv_data(), Path(d) / "cv.pdf")
            # Minimal CV will fit on 1 page
            result = check_page_count(pdf, target_pages=1)
        assert result["meets_target"] is True
        assert result["warning"] is None

    def test_check_page_count_warns_on_underfill(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = render_cv(self._minimal_cv_data(), Path(d) / "cv.pdf")
            # Minimal CV will be 1 page but target is 2 → warning
            result = check_page_count(pdf, target_pages=2)
        assert result["meets_target"] is False
        assert result["warning"] is not None
        assert "adding more content" in result["warning"].lower()


def _minimal_master() -> dict:
    """Self-contained master_cv shape for _apply_adjustments unit tests."""
    return {
        "contact": {
            "name": "T", "email": "t@e.com", "phone": "+1", "location": "Dublin",
            "linkedin": "", "github": "", "visa": "",
        },
        "education": [
            {"degree": "MSc", "institution": "DCU", "dates": "2025", "location": "D", "details": []}
        ],
        "experience": [
            {"id": "e1", "title": "Eng", "company": "Co", "dates": "2024",
             "location": "D", "bullets": ["a", "b", "c", "d"]},
        ],
        "projects": [{"id": "p1", "title": "Proj", "tech": "X", "dates": "2026", "bullets": ["y"]}],
        "skills": {
            "languages": ["Python", "SQL"], "ml_ai": ["PyTorch"],
            "tools": ["Docker"], "other": [],
        },
        "awards": ["Award"],
    }


class TestSectionOrderByVariant:
    def test_keys_match_variants(self):
        from jobpilot.llm import SECTION_ORDER_BY_VARIANT
        assert set(SECTION_ORDER_BY_VARIANT) == set(VALID_VARIANTS)

    def test_each_order_covers_all_sections(self):
        from jobpilot.llm import SECTION_ORDER_BY_VARIANT
        canonical = {"summary", "experience", "education", "projects", "skills", "awards"}
        for order in SECTION_ORDER_BY_VARIANT.values():
            assert set(order) == canonical, f"{order} is not a full permutation"

    def test_grad_puts_projects_and_skills_above_experience(self):
        from jobpilot.llm import SECTION_ORDER_BY_VARIANT
        order = SECTION_ORDER_BY_VARIANT["grad"]
        assert order.index("projects") < order.index("experience")
        assert order.index("skills") < order.index("experience")

    def test_tech_eng_keeps_experience_first(self):
        from jobpilot.llm import SECTION_ORDER_BY_VARIANT
        order = SECTION_ORDER_BY_VARIANT["tech_eng"]
        assert order.index("experience") < order.index("projects")
        # Skills stays high (above education) per the book.
        assert order.index("skills") < order.index("education")

    def test_tailor_cv_attaches_variant_section_order(self):
        from jobpilot.llm import SECTION_ORDER_BY_VARIANT, tailor_cv

        stub = json.dumps({
            "summary": "x", "include_fudan": False,
            "experience_bullet_indices": {}, "project_ids": [],
            "skills": {}, "include_awards": False,
        })
        with patch("jobpilot.llm._call_claude", return_value=stub):
            cv = tailor_cv(
                job={"title": "x", "company": "y", "description": "z"},
                stories=[], profile={}, variant="grad",
            )
        assert cv["section_order"] == SECTION_ORDER_BY_VARIANT["grad"]


class TestPerRoleTechLine:
    def test_validate_techs_filters_to_master_skills(self):
        from jobpilot.llm import _validate_techs
        valid = {"python": "Python", "aws": "AWS"}
        assert _validate_techs(["python", "AWS", "Rust"], valid) == ["Python", "AWS"]

    def test_validate_techs_dedups_preserving_order_and_casing(self):
        from jobpilot.llm import _validate_techs
        valid = {"python": "Python", "sql": "SQL"}
        assert _validate_techs(["SQL", "python", "sql"], valid) == ["SQL", "Python"]

    def test_validate_techs_handles_non_list(self):
        from jobpilot.llm import _validate_techs
        assert _validate_techs(None, {"python": "Python"}) == []

    def test_master_skill_lookup_flattens_all_categories(self):
        from jobpilot.llm import _master_skill_lookup
        lookup = _master_skill_lookup(_minimal_master())
        assert lookup["python"] == "Python"
        assert lookup["docker"] == "Docker"
        assert lookup["pytorch"] == "PyTorch"

    def test_apply_adjustments_attaches_validated_tech_dropping_fabrication(self):
        from jobpilot.llm import _apply_adjustments
        master = _minimal_master()
        result = _apply_adjustments(
            master, {"experience_tech_lines": {"e1": ["Python", "Bogus"]}}
        )
        e1 = next(e for e in result["experience"] if e["id"] == "e1")
        assert e1["tech"] == ["Python"]  # "Bogus" is not in master skills → dropped

    def test_apply_adjustments_defaults_tech_to_empty_list(self):
        from jobpilot.llm import _apply_adjustments
        result = _apply_adjustments(_minimal_master(), {})
        e1 = next(e for e in result["experience"] if e["id"] == "e1")
        assert e1["tech"] == []


class TestSectionOrderPreservation:
    def test_apply_adjustments_preserves_section_order_from_current_cv(self):
        from jobpilot.llm import _apply_adjustments
        current = {"section_order": ["skills", "summary"], "experience": []}
        result = _apply_adjustments(_minimal_master(), {}, current_cv=current)
        assert result["section_order"] == ["skills", "summary"]

    def test_apply_adjustments_omits_section_order_without_current_cv(self):
        from jobpilot.llm import _apply_adjustments
        result = _apply_adjustments(_minimal_master(), {})
        assert "section_order" not in result


class TestEvaluateCVRecruiterChecks:
    def test_prompt_requests_pile_trajectory_weak_bullets(self):
        from jobpilot.llm import evaluate_cv

        captured: dict[str, str] = {}

        def capture(prompt: str, timeout: int = 600, tools: list | None = None) -> str:  # noqa: ARG001
            captured["prompt"] = prompt
            return json.dumps({
                "overall_score": 5, "pile": "maybe",
                "weak_bullets": [], "trajectory": {"assessment": "progression", "note": ""},
            })

        with patch("jobpilot.llm._call_claude", side_effect=capture):
            evaluate_cv(
                {"summary": "x", "experience": [], "skills": {}},
                {"description": "a job"}, "cover letter",
            )

        assert "pile" in captured["prompt"]
        assert "trajectory" in captured["prompt"]
        assert "weak_bullets" in captured["prompt"]
