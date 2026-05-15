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
