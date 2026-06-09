"""The recruiter-scan verdict that `jobpilot tailor` prints and the bot relays.

Pure-function tests — no LLM, no subprocess. Covers the formatter (cli) and the
bot-side extractor, including a round-trip so the markers stay in sync.
"""

from __future__ import annotations

from types import SimpleNamespace

from jobpilot import bot
from jobpilot.cli import _EVAL_END, _EVAL_START, _format_tailor_eval


def _fake_ats(overall=0.62, passed=False, missing=("Kubernetes", "Terraform")):
    return SimpleNamespace(
        overall=overall,
        threshold_passed=passed,
        coverage=SimpleNamespace(missing_must=list(missing)),
    )


_EVAL = {
    "overall_score": 4,
    "pile": "no",
    "would_shortlist": False,
    "trajectory": {"assessment": "flat", "note": "no growth in scope"},
    "weak_bullets": [
        {"bullet": "Was responsible for the data pipeline", "issue": "passive_voice"},
        {"bullet": "Improved performance significantly", "issue": "no_number"},
    ],
    "suggestions": ["Quantify the pipeline impact", "Lead with the ML transition"],
}


class TestFormatTailorEval:
    def test_surfaces_pile_score_and_shortlist(self):
        out = _format_tailor_eval(_fake_ats(), _EVAL)
        assert "Pile: NO (4/10)" in out
        assert "would NOT shortlist" in out

    def test_surfaces_ats_gap(self):
        out = _format_tailor_eval(_fake_ats(), _EVAL)
        assert "ATS: 0.62 (below threshold)" in out
        assert "Kubernetes, Terraform" in out

    def test_surfaces_trajectory_and_weak_bullets(self):
        out = _format_tailor_eval(_fake_ats(), _EVAL)
        assert "Trajectory: flat — no growth in scope" in out
        assert "passive_voice: Was responsible for the data pipeline" in out
        assert "no_number: Improved performance significantly" in out

    def test_is_bracketed_by_markers(self):
        out = _format_tailor_eval(_fake_ats(), _EVAL)
        assert out.startswith(_EVAL_START)
        assert out.endswith(_EVAL_END)

    def test_handles_missing_ats(self):
        out = _format_tailor_eval(None, _EVAL)
        assert "ATS:" not in out
        assert "Pile: NO" in out

    def test_handles_empty_evaluation(self):
        out = _format_tailor_eval(_fake_ats(), {})
        # Still well-formed, just sparse.
        assert out.startswith(_EVAL_START)
        assert out.endswith(_EVAL_END)


class TestExtractEvalBlock:
    def test_round_trip_from_formatter(self):
        block = _format_tailor_eval(_fake_ats(), _EVAL)
        stdout = f"Tailoring...\nRendering...\n{block}\nReady to review:\n  open cv.pdf"
        extracted = bot._extract_eval_block(stdout)
        assert "Pile: NO (4/10)" in extracted
        # Markers themselves are stripped from the relayed message.
        assert _EVAL_START not in extracted
        assert _EVAL_END not in extracted

    def test_returns_empty_when_no_block(self):
        assert bot._extract_eval_block("just some tailor output, no eval") == ""
