"""Tests for jobpilot.discovery.ats_sources — pure unit tests with mocked HTTP."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpilot.discovery import ats_sources as mod
from jobpilot.discovery.ats_sources import (
    discover_all,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    is_dublin_eligible,
)


# ---------------------------------------------------------------------------
# is_dublin_eligible
# ---------------------------------------------------------------------------

class TestDublinFilter:
    @pytest.mark.parametrize("loc,reason", [
        ("Dublin, Ireland", "dublin"),
        ("Dublin", "dublin"),
        ("Ireland (Remote)", "dublin"),
        ("Dublin, Ireland (Hybrid)", "dublin"),
        ("London, UK / Dublin, Ireland", "dublin"),
        ("Amsterdam, The Netherlands; Dublin, Ireland", "dublin"),
    ])
    def test_dublin_strings_match(self, loc, reason):
        ok, got = is_dublin_eligible(loc)
        assert ok is True
        assert got == reason

    @pytest.mark.parametrize("loc,reason", [
        ("Remote - EMEA", "remote_emea"),
        ("Remote (EMEA)", "remote_emea"),
        ("Remote - Europe", "remote_emea"),
        ("Anywhere", "remote_emea"),
        ("Fully Remote", "remote_emea"),
    ])
    def test_remote_emea_match(self, loc, reason):
        ok, got = is_dublin_eligible(loc)
        assert ok is True
        assert got == reason

    @pytest.mark.parametrize("loc", [
        "Remote - United States",
        "Remote - US",
        "Remote - Canada",
        "US Remote",
        "Remote - North America",
    ])
    def test_us_or_na_explicitly_rejected(self, loc):
        ok, got = is_dublin_eligible(loc)
        assert ok is False
        assert got == "us_or_na_only"

    @pytest.mark.parametrize("loc", [
        "Paris",
        "San Francisco",
        "London",
        "Berlin",
        "Toronto",
        "",
    ])
    def test_other_locations_rejected(self, loc):
        ok, got = is_dublin_eligible(loc)
        assert ok is False
        assert got == "other_location"

    def test_negative_pattern_beats_positive(self):
        # If a string says both "remote - us" and "ireland" somehow, hard-negative wins
        ok, got = is_dublin_eligible("Remote - US (ireland sometimes ok)")
        assert ok is False
        assert got == "us_or_na_only"


# ---------------------------------------------------------------------------
# Per-ATS fetchers — mock _http_get_json
# ---------------------------------------------------------------------------

GREENHOUSE_FIXTURE = {
    "jobs": [
        {
            "id": 12345,
            "title": "Senior Software Engineer",
            "location": {"name": "Dublin, Ireland"},
            "content": "<p>Build <b>cool</b> things.</p>",
            "absolute_url": "https://example.com/jobs/12345",
            "updated_at": "2026-05-01T10:00:00Z",
        },
        {
            "id": 67890,
            "title": "ML Engineer",
            "location": {"name": "San Francisco, CA"},
            "content": "Should be filtered out.",
            "absolute_url": "https://example.com/jobs/67890",
            "updated_at": "2026-05-02T10:00:00Z",
        },
    ]
}


def test_fetch_greenhouse_normalizes_and_filters():
    with patch.object(mod, "_http_get_json", return_value=GREENHOUSE_FIXTURE):
        jobs = fetch_greenhouse("acme", "Acme Corp")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["id"] == "acme_12345"
    assert j["title"] == "Senior Software Engineer"
    assert j["company"] == "Acme Corp"
    assert j["location"] == "Dublin, Ireland"
    assert j["description"] == "Build cool things."  # HTML stripped
    assert j["url"] == "https://example.com/jobs/12345"
    assert j["source"] == "ats:greenhouse:acme"
    assert j["ats_type"] == "greenhouse"
    assert j["company_slug"] == "acme"
    assert j["dublin_match"] == "dublin"
    assert j["posted_at"] == "2026-05-01T10:00:00Z"


LEVER_FIXTURE = [
    {
        "id": "abc-123",
        "text": "Backend Engineer",
        "categories": {"location": "Remote - EMEA", "team": "Engineering"},
        "descriptionPlain": "Plain text description.",
        "hostedUrl": "https://jobs.lever.co/acme/abc-123",
        "createdAt": 1714521600000,  # ms
    },
    {
        "id": "xyz-999",
        "text": "Sales Rep",
        "categories": {"location": "New York"},
        "descriptionPlain": "filtered out",
        "hostedUrl": "https://jobs.lever.co/acme/xyz-999",
    },
]


def test_fetch_lever_normalizes_and_filters():
    with patch.object(mod, "_http_get_json", return_value=LEVER_FIXTURE):
        jobs = fetch_lever("acme", "Acme Corp")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["id"] == "acme_abc-123"
    assert j["title"] == "Backend Engineer"
    assert j["dublin_match"] == "remote_emea"
    assert j["url"] == "https://jobs.lever.co/acme/abc-123"
    assert j["ats_type"] == "lever"
    # Lever's createdAt ms epoch should normalize to ISO
    assert j["posted_at"].startswith("2024-")


ASHBY_FIXTURE = {
    "jobs": [
        {
            "id": "ashby-job-1",
            "title": "Data Scientist",
            "location": "Dublin /",
            "secondaryLocations": [{"location": "London"}],
            "descriptionHtml": "<p>Do <em>data</em> science.</p>",
            "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-job-1",
            "publishedAt": "2026-04-30T08:00:00Z",
        },
        {
            "id": "ashby-job-2",
            "title": "Account Exec",
            "location": "United States /",
            "descriptionHtml": "<p>Filtered out</p>",
            "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-job-2",
        },
    ]
}


def test_fetch_ashby_normalizes_and_filters():
    with patch.object(mod, "_http_get_json", return_value=ASHBY_FIXTURE):
        jobs = fetch_ashby("acme", "Acme Corp")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["id"] == "acme_ashby-job-1"
    assert j["title"] == "Data Scientist"
    assert "Dublin" in j["location"]
    assert "London" in j["location"]  # secondary location merged in
    assert j["description"] == "Do data science."  # HTML stripped
    assert j["ats_type"] == "ashby"
    assert j["dublin_match"] == "dublin"


# ---------------------------------------------------------------------------
# discover_all — driven by target_companies.json
# ---------------------------------------------------------------------------

def test_discover_all_only_polls_active_tier1(tmp_path: Path):
    """Cold companies, non-Tier-1, and unsupported ATS types must not be polled."""
    targets = {
        "active": [
            {"name": "Active GH", "tier": 1, "ats": "greenhouse", "ats_slug": "active-gh"},
            {"name": "Active Lever", "tier": 1, "ats": "lever", "ats_slug": "active-lever"},
            {"name": "Tier2 Skip", "tier": 2, "ats": "custom", "linkedin_company": "skip"},
            {"name": "Workday Skip", "tier": 1, "ats": "workday"},
            {"name": "No slug", "tier": 1, "ats": "greenhouse"},  # missing slug → skipped
        ],
        "cold": [
            {"name": "Cold GH", "ats": "greenhouse", "ats_slug": "cold-gh"},
        ],
    }
    target_file = tmp_path / "target_companies.json"
    target_file.write_text(json.dumps(targets))

    called: list[tuple[str, str]] = []

    def fake_get(url, **_):
        if "active-gh" in url:
            called.append(("greenhouse", "active-gh"))
            return GREENHOUSE_FIXTURE
        if "active-lever" in url:
            called.append(("lever", "active-lever"))
            return LEVER_FIXTURE
        if "cold-gh" in url:
            called.append(("greenhouse", "cold-gh"))
            return GREENHOUSE_FIXTURE
        pytest.fail(f"Unexpected URL: {url}")

    with patch.object(mod, "_http_get_json", side_effect=fake_get):
        jobs, stats = discover_all(target_path=target_file)

    polled_slugs = {slug for _, slug in called}
    assert polled_slugs == {"active-gh", "active-lever"}, (
        "Should poll exactly the two active Tier-1 entries with known ATS + slug"
    )
    assert stats["companies_polled"] == 2
    # Each fixture yields 1 Dublin-eligible job after filter
    assert stats["total_jobs"] == 2
    assert not stats["errors"]


def test_discover_all_continues_on_per_company_error(tmp_path: Path):
    targets = {
        "active": [
            {"name": "Good", "tier": 1, "ats": "greenhouse", "ats_slug": "good-co"},
            {"name": "Bad", "tier": 1, "ats": "greenhouse", "ats_slug": "bad-co"},
        ]
    }
    target_file = tmp_path / "target_companies.json"
    target_file.write_text(json.dumps(targets))

    def fake_get(url, **_):
        if "good-co" in url:
            return GREENHOUSE_FIXTURE
        raise TimeoutError("simulated")

    with patch.object(mod, "_http_get_json", side_effect=fake_get):
        jobs, stats = discover_all(target_path=target_file)

    assert stats["total_jobs"] == 1  # only good-co's one Dublin job
    assert len(stats["errors"]) == 1
    assert "bad-co" in stats["errors"][0]
    # Per-company stats record both
    assert "good-co" in stats["per_company"]
    assert "bad-co" in stats["per_company"]
    assert stats["per_company"]["bad-co"]["error"] is not None
