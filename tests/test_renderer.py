import os
import tempfile
from pathlib import Path

import pytest

from jobpilot.renderer import escape_latex, render_cv, render_cover_letter


class TestEscapeLatex:
    def test_ampersand(self):
        assert escape_latex("A & B") == r"A \& B"

    def test_percent(self):
        assert escape_latex("100%") == r"100\%"

    def test_dollar(self):
        assert escape_latex("$500") == r"\$500"

    def test_hash(self):
        assert escape_latex("item #1") == r"item \#1"

    def test_underscore(self):
        assert escape_latex("my_var") == r"my\_var"

    def test_braces(self):
        assert escape_latex("{x}") == r"\{x\}"

    def test_tilde(self):
        assert escape_latex("~home") == r"\textasciitilde{}home"

    def test_caret(self):
        assert escape_latex("x^2") == r"x\textasciicircum{}2"

    def test_backslash(self):
        assert escape_latex("a\\b") == r"a\textbackslash{}b"

    def test_empty_string(self):
        assert escape_latex("") == ""

    def test_no_special_chars(self):
        assert escape_latex("Hello World") == "Hello World"

    def test_mixed_special_chars(self):
        result = escape_latex("Price: $5 & 10% off")
        assert r"\$" in result
        assert r"\&" in result
        assert r"\%" in result

    def test_backslash_before_others(self):
        # Backslash must be escaped first to avoid double-escaping
        result = escape_latex("a\\b & c")
        assert r"\textbackslash{}" in result
        assert r"\&" in result


class TestRenderCV:
    def test_produces_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            data = {
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
            path = render_cv(data, Path(d) / "cv.pdf")
            assert path.exists()
            assert os.path.getsize(path) > 1000

    def test_handles_special_chars_in_content(self):
        with tempfile.TemporaryDirectory() as d:
            data = {
                "name": "Test & User",
                "email": "test@example.com",
                "location": "Dublin",
                "summary": "Improved metrics by 50% using C++ & Python.",
                "experience": [],
                "skills": ["C#", "node.js"],
            }
            path = render_cv(data, Path(d) / "cv.pdf")
            assert path.exists()


class TestSectionOrderAndTechLine:
    @staticmethod
    def _cv(section_order=None):
        data = {
            "name": "Test User",
            "email": "test@example.com",
            "phone": "+353 87 000 0000",
            "location": "Dublin",
            "summary": "A test summary.",
            "experience": [
                {
                    "title": "Engineer",
                    "company": "TestCo",
                    "dates": "2024",
                    "location": "Dublin",
                    "bullets": ["Did things"],
                    "tech": ["Python", "AWS"],
                }
            ],
            "projects": [
                {"title": "Proj", "tech": "LangGraph", "dates": "2026", "bullets": ["Built it"]}
            ],
            "education": [
                {"degree": "MSc", "institution": "DCU", "dates": "2025", "location": "Dublin", "details": []}
            ],
            "skills": {"Languages": ["Python"]},
            "awards": ["Award"],
        }
        if section_order is not None:
            data["section_order"] = section_order
        return data

    def test_grad_order_puts_projects_and_skills_above_experience(self):
        from jobpilot.ats import extract_pdf_text

        with tempfile.TemporaryDirectory() as d:
            data = self._cv(["summary", "skills", "projects", "experience", "education", "awards"])
            pdf = render_cv(data, Path(d) / "cv.pdf")
            txt = extract_pdf_text(pdf)
        assert 0 <= txt.find("Projects") < txt.find("Experience")
        assert 0 <= txt.find("Skills") < txt.find("Experience")

    def test_default_order_keeps_experience_before_projects(self):
        from jobpilot.ats import extract_pdf_text

        with tempfile.TemporaryDirectory() as d:
            # No section_order → renderer injects DEFAULT_SECTION_ORDER.
            pdf = render_cv(self._cv(None), Path(d) / "cv.pdf")
            txt = extract_pdf_text(pdf)
        assert 0 <= txt.find("Experience") < txt.find("Projects")

    def test_per_role_technologies_line_rendered(self):
        from jobpilot.ats import extract_pdf_text

        with tempfile.TemporaryDirectory() as d:
            pdf = render_cv(self._cv(None), Path(d) / "cv.pdf")
            txt = extract_pdf_text(pdf)
        assert "Technologies" in txt

    def test_no_tech_line_when_absent(self):
        from jobpilot.ats import extract_pdf_text

        data = self._cv(None)
        data["experience"][0].pop("tech")
        with tempfile.TemporaryDirectory() as d:
            pdf = render_cv(data, Path(d) / "cv.pdf")
            txt = extract_pdf_text(pdf)
        assert "Technologies" not in txt


class TestRenderCoverLetter:
    def test_produces_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            data = {
                "name": "Test User",
                "date": "April 6, 2026",
                "company": "TestCo",
                "job_title": "Engineer",
                "body": "Dear Hiring Manager, I am interested in this role.",
            }
            path = render_cover_letter(data, Path(d) / "cl.pdf")
            assert path.exists()
            assert os.path.getsize(path) > 1000
