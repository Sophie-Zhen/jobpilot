"""Tests for `_job_folder` — the human-readable output folder slugifier."""
from jobpilot.cli import _job_folder, _slug_for_folder


class TestSlugForFolder:
    def test_basic_lowercase(self):
        assert _slug_for_folder("Intercom") == "intercom"

    def test_punctuation_to_underscore(self):
        assert _slug_for_folder("nineDots.io") == "ninedots_io"

    def test_collapse_repeats(self):
        assert _slug_for_folder("a -- b") == "a_b"

    def test_strip_edges(self):
        assert _slug_for_folder("(Hybrid)") == "hybrid"

    def test_empty(self):
        assert _slug_for_folder("") == ""

    def test_none_safe(self):
        assert _slug_for_folder(None) == ""


class TestJobFolder:
    def test_intercom_greenhouse(self):
        job = {
            "id": "intercom_7925313",
            "company": "Intercom",
            "title": "Senior Product Engineer - Pricing & Packaging",
        }
        # 6 words: Senior Product Engineer - Pricing &  → "senior_product_engineer_pricing"
        assert _job_folder(job) == "intercom_senior_product_engineer_pricing_7925313"

    def test_opencli_linkedin(self):
        job = {
            "id": "opencli_linkedin_4411594967",
            "company": "nineDots.io",
            "title": "Senior Data Scientist (AI Engineering Focus)",
        }
        out = _job_folder(job)
        assert out.startswith("ninedots_io_senior_data_scientist")
        assert out.endswith("_4411594967")

    def test_title_long_truncated(self):
        job = {
            "id": "x_42",
            "company": "Acme",
            "title": "A Very Long Job Title With Many Words Beyond Six Cutoff Point",
        }
        out = _job_folder(job)
        # First 6 words only, slug then capped at 40 chars
        assert "many_words" not in out  # 7th+ words dropped
        assert "_42" in out

    def test_fallback_to_job_id_when_empty(self):
        job = {"id": "fallback_xyz", "company": "", "title": ""}
        # No numeric suffix → falls back to last 8 chars after stripping _
        assert _job_folder(job) == "back_xyz"

    def test_total_fallback(self):
        # job_id empty too → "unknown"
        assert _job_folder({}) == "unknown"

    def test_numeric_only_id(self):
        job = {"id": "7925313", "company": "X", "title": "Eng"}
        assert _job_folder(job) == "x_eng_7925313"

    def test_collision_safety_via_id_suffix(self):
        # Two different IT Systems Engineer postings at Intercom
        j1 = {"id": "intercom_7918715", "company": "Intercom", "title": "IT Systems Engineer"}
        j2 = {"id": "intercom_7930382", "company": "Intercom", "title": "IT Systems Engineer"}
        assert _job_folder(j1) != _job_folder(j2)
        assert _job_folder(j1).endswith("_7918715")
        assert _job_folder(j2).endswith("_7930382")
