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
