from __future__ import annotations

from typing import Any, TypedDict


class JobPilotState(TypedDict, total=False):
    profile: dict[str, Any]
    stories: list[dict[str, Any]]
    learning_progress: dict[str, Any]
    search_query: str
    jobs_found: list[dict[str, Any]]
    scored_jobs: list[dict[str, Any]]
    approved_jobs: list[dict[str, Any]]
    tailored_resumes: list[dict[str, Any]]
    cover_letters: list[dict[str, Any]]
    pdf_paths: list[str]
    evaluations: list[dict[str, Any]]
    applications: list[dict[str, Any]]
    review_notes: str
    learning_plan: str
    messages: list[str]
