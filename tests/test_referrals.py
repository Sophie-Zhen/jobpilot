"""Tests for referral discovery from a LinkedIn Connections.csv export."""

from __future__ import annotations

from pathlib import Path

from jobpilot.referrals import (
    Connection,
    find_referrers,
    load_connections,
    referral_hint,
    top_companies,
)

# A realistic LinkedIn export: a few "Notes:" preamble lines, a blank line, then
# the real header row, then connections.
_SAMPLE_CSV = '''Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Aoife,Murphy,https://www.linkedin.com/in/aoifemurphy,,Stripe,Software Engineer,01 Mar 2025
Liam,Chen,https://www.linkedin.com/in/liamchen,liam@example.com,Stripe Payments Europe,Engineering Manager,15 Feb 2025
Niamh,Byrne,https://www.linkedin.com/in/niamhbyrne,,Google Ireland,Recruiter,02 Jan 2025
Sean,O'Brien,https://www.linkedin.com/in/seanobrien,,Metabase,Data Engineer,10 Dec 2024
,,,,,,
'''


def _write_csv(tmp_path: Path, content: str = _SAMPLE_CSV) -> Path:
    p = tmp_path / "connections.csv"
    p.write_text(content, encoding="utf-8")
    return p


class TestLoadConnections:
    def test_skips_preamble_and_parses_rows(self, tmp_path):
        conns = load_connections(_write_csv(tmp_path))
        assert len(conns) == 4  # the trailing all-empty row is dropped
        assert conns[0].name == "Aoife Murphy"
        assert conns[0].company == "Stripe"
        assert conns[0].position == "Software Engineer"

    def test_missing_file_returns_empty(self, tmp_path):
        assert load_connections(tmp_path / "nope.csv") == []

    def test_malformed_no_header_returns_empty(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("just some text\nno header here\n", encoding="utf-8")
        assert load_connections(p) == []


class TestFindReferrers:
    def _conns(self):
        return [
            Connection("Aoife", "Murphy", "Stripe", "Software Engineer"),
            Connection("Liam", "Chen", "Stripe Payments Europe", "Engineering Manager"),
            Connection("Niamh", "Byrne", "Google Ireland", "Recruiter"),
            Connection("Sean", "O'Brien", "Metabase", "Data Engineer"),
        ]

    def test_exact_match(self):
        refs = find_referrers("Google Ireland", self._conns())
        assert [r.name for r in refs] == ["Niamh Byrne"]

    def test_token_subset_matches_both_directions(self):
        # "Stripe" job should match both the "Stripe" and "Stripe Payments Europe" connections.
        refs = find_referrers("Stripe", self._conns())
        assert {r.name for r in refs} == {"Aoife Murphy", "Liam Chen"}

    def test_no_substring_false_positive(self):
        # "Meta" must NOT match "Metabase" (token-subset, not substring).
        refs = find_referrers("Meta", self._conns())
        assert refs == []

    def test_normalizes_company_suffixes(self):
        # "Google Ireland Ltd" normalizes the same as "Google Ireland".
        refs = find_referrers("Google Ireland Ltd", self._conns())
        assert [r.name for r in refs] == ["Niamh Byrne"]

    def test_no_match_returns_empty(self):
        assert find_referrers("Amazon", self._conns()) == []

    def test_empty_company_returns_empty(self):
        assert find_referrers("", self._conns()) == []


class TestTopCompanies:
    def test_ranks_by_count(self):
        conns = [
            Connection("A", "A", "Stripe"),
            Connection("B", "B", "Stripe Payments Europe"),  # normalizes differently → own bucket
            Connection("C", "C", "Google"),
            Connection("D", "D", "Google"),
            Connection("E", "E", "Google"),
        ]
        top = top_companies(conns, n=5)
        assert top[0] == ("Google", 3)


class TestReferralHint:
    def test_hint_lists_names_and_positions(self):
        conns = [Connection("Aoife", "Murphy", "Stripe", "Software Engineer")]
        hint = referral_hint("Stripe", conns)
        assert "1 connection(s) at Stripe" in hint
        assert "Aoife Murphy (Software Engineer)" in hint

    def test_hint_empty_when_no_referrers(self):
        assert referral_hint("Amazon", [Connection("A", "A", "Stripe")]) == ""

    def test_hint_truncates_with_more(self):
        conns = [Connection(f"P{i}", "X", "Stripe") for i in range(7)]
        hint = referral_hint("Stripe", conns, limit=5)
        assert "+2 more" in hint
