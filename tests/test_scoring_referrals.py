"""Referral boost in job scoring — a warm path floats a job up the pipeline."""

from __future__ import annotations

from jobpilot import agents
from jobpilot.job_sources import _REFERRAL_BOOST, score_jobs
from jobpilot.referrals import Connection


def _jobs():
    return [
        {"id": "a", "title": "ML Engineer", "company": "Stripe", "skills": ["python"]},
        {"id": "b", "title": "ML Engineer", "company": "Acme", "skills": ["python"]},
    ]


class TestScoreJobsReferralBoost:
    def test_no_connections_leaves_score_unchanged(self):
        scored = score_jobs(_jobs(), {"python"})
        assert scored[0]["referral_count"] == 0
        # match_score == score when no referral boost applied
        assert all(j["score"] == j["match_score"] for j in scored)

    def test_referral_boosts_and_reorders(self):
        conns = [Connection("Aoife", "Murphy", "Stripe", "Software Engineer")]
        scored = score_jobs(_jobs(), {"python"}, connections=conns)
        by_id = {j["id"]: j for j in scored}
        # Stripe job gets the boost; Acme job does not.
        assert by_id["a"]["referral_count"] == 1
        assert by_id["b"]["referral_count"] == 0
        assert by_id["a"]["score"] == round(min(by_id["a"]["match_score"] + _REFERRAL_BOOST, 1.0), 2)
        # The referable job sorts first.
        assert scored[0]["id"] == "a"

    def test_score_capped_at_one(self):
        # A perfect-match job at a connected company must not exceed 1.0.
        jobs = [{"id": "x", "title": "ML Engineer", "company": "Stripe",
                 "skills": ["python"], }]
        conns = [Connection("A", "B", "Stripe")]
        scored = score_jobs(jobs, {"python"}, preferred_keywords=["ml engineer"],
                            target_roles=["ml engineer"], connections=conns)
        assert scored[0]["score"] <= 1.0


class TestScoreJobsNodeReferralBoost:
    def test_node_applies_boost(self, monkeypatch):
        monkeypatch.setattr(
            agents, "load_connections",
            lambda _path: [Connection("A", "B", "Stripe", "SWE")],
        )

        class _S:
            connections_csv = "ignored.csv"

        state = {
            "profile": {"skills": ["python"]},
            "jobs_found": _jobs(),
        }
        out = agents.score_jobs_node(state, settings=_S())
        scored = {j["id"]: j for j in out["scored_jobs"]}
        assert scored["a"]["referral_count"] == 1
        assert scored["a"]["score"] == round(min(scored["a"]["skill_score"] + agents._REFERRAL_BOOST, 1.0), 2)
        assert scored["b"]["referral_count"] == 0
        # Referable job ranks first.
        assert out["scored_jobs"][0]["id"] == "a"
