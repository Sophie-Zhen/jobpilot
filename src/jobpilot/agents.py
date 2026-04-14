from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from jobpilot.config import Settings
from jobpilot.job_sources import search_jobs_multi_query
from jobpilot.profile import load_profile
from jobpilot.stories import StoryBank
from jobpilot.storage import StoryStore


def load_profile_node(_: dict[str, Any], settings: Settings) -> dict[str, Any]:
    profile = load_profile(settings)
    return {"profile": profile}


def load_stories_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    bank = StoryBank()
    stories = bank.list_stories()
    messages = state.get("messages", [])
    messages.append(f"Loaded {len(stories)} stories from story bank.")
    story_dicts = [s.model_dump() for s in stories]
    return {"stories": story_dicts, "messages": messages}


def search_jobs_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    profile = state.get("profile", {})
    explicit_query = state.get("search_query")
    if explicit_query:
        query_candidates = [explicit_query]
    else:
        roles = [r for r in profile.get("target_roles", []) if r]
        seen: set[str] = set()
        query_candidates = []
        for r in roles[:4]:
            if r not in seen:
                query_candidates.append(r)
                seen.add(r)

    location = profile.get("location", "Dublin, Ireland")
    messages = state.get("messages", [])

    # Primary: Claude Code web search
    try:
        from jobpilot.llm import search_jobs_web
        from jobpilot.job_sources import _apply_profile_filters
        jobs = search_jobs_web(query_candidates, location=location)
        jobs = _apply_profile_filters(jobs, profile, limit=settings.daily_limit)
        query = ", ".join(query_candidates)
        messages.append(f"Fetched {len(jobs)} new jobs via web search | queries='{query}'.")
        return {"search_query": query, "jobs_found": jobs, "messages": messages}
    except Exception as exc:
        messages.append(f"Web search failed ({exc}), falling back to API search.")

    # Fallback: RapidAPI sources
    jobs, search_info, query = search_jobs_multi_query(
        settings, query_candidates=query_candidates, profile=profile
    )
    messages.append(f"Fetched {len(jobs)} jobs | query='{query}' | {search_info}.")
    return {"search_query": query, "jobs_found": jobs, "messages": messages}


def score_jobs_node(state: dict[str, Any]) -> dict[str, Any]:
    profile_skills = set(state.get("profile", {}).get("skills", []))
    learning_progress = state.get("learning_progress", {})
    bonus_skills = set(learning_progress.get("score_bonus_skills", []))
    scored = []
    for job in state.get("jobs_found", []):
        job_skills = set(job.get("skills", []))
        overlap = len(profile_skills.intersection(job_skills))
        bonus_overlap = len(bonus_skills.intersection(job_skills))
        weighted_overlap = overlap + (0.5 * bonus_overlap)
        score = round(weighted_overlap / max(len(job_skills), 1), 2)
        scored.append({**job, "score": score, "skill_bonus": bonus_overlap})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"scored_jobs": scored}


def review_jobs_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    scored = state.get("scored_jobs", [])

    if settings.auto_approve or settings.scheduled:
        if settings.scheduled:
            _save_daily_results(scored)
            return {"approved_jobs": [], "review_notes": "SCHEDULED_RUN — review with `jobpilot review`"}
        return {"approved_jobs": scored[:2], "review_notes": "AUTO_APPROVE enabled"}

    review_input = interrupt(
        {
            "type": "review_jobs",
            "prompt": "Review top jobs and select approved job IDs.",
            "jobs": [
                {
                    "id": job["id"],
                    "title": job["title"],
                    "company": job["company"],
                    "score": job["score"],
                }
                for job in scored
            ],
        }
    )
    approved_ids = set(review_input.get("approved_job_ids", []))
    notes = review_input.get("notes", "")
    approved_jobs = [job for job in scored if job["id"] in approved_ids]
    return {"approved_jobs": approved_jobs, "review_notes": notes}


def _save_daily_results(scored_jobs: list[dict[str, Any]]) -> None:
    results_dir = Path("data/daily_results")
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{date.today().isoformat()}.json"
    existing = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.extend(scored_jobs)
    path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def fetch_jds_node(state: dict[str, Any]) -> dict[str, Any]:
    """Fetch full job descriptions for approved jobs."""
    approved = state.get("approved_jobs", [])
    messages = state.get("messages", [])
    enriched = []
    for job in approved:
        url = job.get("url", "")
        if url and len(job.get("description", "")) < 200:
            try:
                from jobpilot.llm import fetch_full_jd
                full_jd = fetch_full_jd(url)
                if full_jd and len(full_jd) > len(job.get("description", "")):
                    job = {**job, "full_description": full_jd}
                    messages.append(f"Fetched full JD for {job['title']} at {job['company']}.")
            except Exception as exc:
                messages.append(f"Could not fetch JD for {job.get('title', '')}: {exc}")
        enriched.append(job)
    return {"approved_jobs": enriched, "messages": messages}


def tailor_resume_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    approved = state.get("approved_jobs", [])
    if not approved or settings.scheduled:
        return {"tailored_resumes": [], "cover_letters": []}

    profile = state.get("profile", {})
    bank = StoryBank()

    tailored_resumes = []
    cover_letters = []

    for job in approved:
        job_text = f"{job.get('title', '')} {job.get('full_description', job.get('description', ''))}"
        relevant_stories = bank.find_similar(job_text, top_k=8)

        try:
            from jobpilot.llm import tailor_cv, generate_cover_letter, classify_role_level

            role_level = classify_role_level(job)
            cv_data = tailor_cv(job, relevant_stories, profile, role_level=role_level)
            cv_data["role_level"] = role_level
            cv_data["job_id"] = job["id"]
            tailored_resumes.append(cv_data)

            cover_letter = generate_cover_letter(job, relevant_stories, profile, role_level=role_level)
            cover_letters.append({
                "job_id": job["id"],
                "text": cover_letter,
            })
        except Exception as exc:
            tailored_resumes.append({
                "job_id": job["id"],
                "error": str(exc),
                "summary": f"Target role: {job['title']} at {job['company']}",
                "experience": [],
                "skills": profile.get("skills", [])[:10],
                "selected_stories": [],
            })
            cover_letters.append({
                "job_id": job["id"],
                "text": f"[Cover letter generation failed: {exc}]",
            })

    return {"tailored_resumes": tailored_resumes, "cover_letters": cover_letters}


def review_tailored_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    if settings.auto_approve or settings.scheduled:
        return {}

    resumes = state.get("tailored_resumes", [])
    letters = state.get("cover_letters", [])
    approved = state.get("approved_jobs", [])

    approved_resumes = []
    approved_letters = []

    for resume, letter, job in zip(resumes, letters, approved):
        if resume.get("error"):
            print(f"\nWarning: Tailoring failed for {job.get('title')}: {resume['error']}")
            continue

        review_input = interrupt({
            "type": "review_tailored",
            "job_id": job["id"],
            "job_title": job.get("title", ""),
            "company": job.get("company", ""),
            "cv_summary": resume.get("summary", ""),
            "cv_experience": resume.get("experience", []),
            "cv_skills": resume.get("skills", []),
            "cover_letter_text": letter.get("text", ""),
            "selected_stories": resume.get("selected_stories", []),
        })

        action = review_input.get("action", "approve")
        if action == "reject":
            continue
        if action == "edit":
            edits = review_input.get("edits", {})
            if edits.get("cv_summary"):
                resume["summary"] = edits["cv_summary"]
            if edits.get("cv_experience"):
                resume["experience"] = edits["cv_experience"]
            if edits.get("cv_skills"):
                resume["skills"] = edits["cv_skills"]
            if edits.get("cover_letter_text"):
                letter["text"] = edits["cover_letter_text"]

        approved_resumes.append(resume)
        approved_letters.append(letter)

    return {"tailored_resumes": approved_resumes, "cover_letters": approved_letters}


def evaluate_cv_node(state: dict[str, Any]) -> dict[str, Any]:
    """Run independent evaluation on each tailored CV."""
    resumes = state.get("tailored_resumes", [])
    letters = state.get("cover_letters", [])
    approved = state.get("approved_jobs", [])
    evaluations = []

    resume_by_job = {r.get("job_id"): r for r in resumes}
    letter_by_job = {l.get("job_id"): l for l in letters}

    for job in approved:
        resume = resume_by_job.get(job["id"], {})
        letter = letter_by_job.get(job["id"], {})
        if resume.get("error"):
            evaluations.append({"job_id": job["id"], "error": "CV tailoring failed"})
            continue
        try:
            from jobpilot.llm import evaluate_cv
            evaluation = evaluate_cv(resume, job, letter.get("text", ""))
            evaluation["job_id"] = job["id"]
            evaluations.append(evaluation)
        except Exception as exc:
            evaluations.append({"job_id": job["id"], "error": str(exc)})

    return {"evaluations": evaluations}


def review_evaluation_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Show evaluation results. User can finalize or revise."""
    if settings.auto_approve or settings.scheduled:
        return {}

    evaluations = state.get("evaluations", [])
    resumes = state.get("tailored_resumes", [])
    letters = state.get("cover_letters", [])

    for evaluation in evaluations:
        job_id = evaluation.get("job_id")
        if evaluation.get("error"):
            continue

        review_input = interrupt({
            "type": "review_evaluation",
            "job_id": job_id,
            "overall_score": evaluation.get("overall_score", 0),
            "keyword_coverage": evaluation.get("keyword_coverage", {}),
            "red_flags": evaluation.get("red_flags", []),
            "strengths": evaluation.get("strengths", []),
            "suggestions": evaluation.get("suggestions", []),
            "ats_issues": evaluation.get("ats_issues", []),
            "would_shortlist": evaluation.get("would_shortlist", False),
        })

        action = review_input.get("action", "finalize")
        if action == "revise":
            edits = review_input.get("edits", {})
            for resume in resumes:
                if resume.get("job_id") == job_id:
                    if edits.get("summary"):
                        resume["summary"] = edits["summary"]
                    if edits.get("skills"):
                        resume["skills"] = edits["skills"]
                    break
            for letter in letters:
                if letter.get("job_id") == job_id:
                    if edits.get("cover_letter_text"):
                        letter["text"] = edits["cover_letter_text"]
                    break

    return {"tailored_resumes": resumes, "cover_letters": letters}


def render_pdfs_node(state: dict[str, Any], settings: Settings) -> dict[str, Any]:
    resumes = state.get("tailored_resumes", [])
    letters = state.get("cover_letters", [])
    pdf_paths = []

    if not resumes:
        return {"pdf_paths": []}

    try:
        from jobpilot.renderer import render_cv, render_cover_letter
    except ImportError:
        return {"pdf_paths": [], "messages": state.get("messages", []) + ["PDF renderer not available."]}

    for resume, letter in zip(resumes, letters):
        job_id = resume.get("job_id", "unknown")
        output_dir = Path(settings.output_dir) / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # tailor_cv() already returns contact info from master_cv.json
        cv_data = resume

        try:
            cv_path = render_cv(cv_data, output_dir / "cv.pdf")
            pdf_paths.append(str(cv_path))
        except Exception as exc:
            print(f"CV render failed for {job_id}: {exc}")

        try:
            cl_data = {
                "name": resume.get("name", ""),
                "date": date.today().strftime("%B %d, %Y"),
                "company": resume.get("company", ""),
                "job_title": resume.get("job_title", ""),
                "body": letter.get("text", ""),
            }
            cl_path = render_cover_letter(cl_data, output_dir / "cover_letter.pdf")
            pdf_paths.append(str(cl_path))
        except Exception as exc:
            print(f"Cover letter render failed for {job_id}: {exc}")

    messages = state.get("messages", [])
    messages.append(f"Generated {len(pdf_paths)} PDFs.")
    return {"pdf_paths": pdf_paths, "messages": messages}


def apply_jobs_node(state: dict[str, Any], store: StoryStore) -> dict[str, Any]:
    applications = []
    resume_by_job = {r["job_id"]: r for r in state.get("tailored_resumes", [])}
    letter_by_job = {l["job_id"]: l for l in state.get("cover_letters", [])}
    eval_by_job = {e["job_id"]: e for e in state.get("evaluations", [])}
    today = date.today().isoformat()

    for job in state.get("approved_jobs", []):
        resume = resume_by_job.get(job["id"], {})
        letter = letter_by_job.get(job["id"], {})
        evaluation = eval_by_job.get(job["id"], {})
        application = {
            "job_id": job["id"],
            "company": job["company"],
            "title": job["title"],
            "url": job.get("url", ""),
            "full_description": job.get("full_description", job.get("description", "")),
            "status": "tailored",
            "role_level": resume.get("role_level", "unknown"),
            "status_history": [
                {"status": "discovered", "date": today, "notes": ""},
                {"status": "tailored", "date": today, "notes": f"Role level: {resume.get('role_level', 'unknown')}"},
            ],
            "dates": {
                "discovered": today,
                "tailored": today,
                "submitted": None,
                "interview": None,
                "response": None,
            },
            "resume_summary": resume.get("summary", ""),
            "cover_letter_preview": letter.get("text", "")[:200],
            "pdf_paths": [p for p in state.get("pdf_paths", []) if job["id"] in p],
            "evaluation_score": evaluation.get("overall_score"),
            "next_action": None,
            "feedback": None,
        }
        store.save_application(application)
        applications.append(application)

    _persist_applications(applications)
    return {"applications": applications}


def _persist_applications(new_apps: list[dict[str, Any]]) -> None:
    apps_path = Path("data/applications.json")
    existing = []
    if apps_path.exists():
        try:
            existing = json.loads(apps_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing.extend(new_apps)
    apps_path.parent.mkdir(parents=True, exist_ok=True)
    apps_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def learning_progress_node(state: dict[str, Any]) -> dict[str, Any]:
    profile_skills = set(state.get("profile", {}).get("skills", []))
    completed_skills = {
        "python", "sql", "pytorch", "llm", "nlp", "transformers",
        "rag", "prompt engineering", "scikit-learn", "spacy", "pandas",
        "numpy", "git", "docker", "linux", "prolog", "deep learning",
        "machine learning", "data wrangling", "workflow automation",
    }
    in_progress_skills = {"kubernetes", "mlops", "terraform", "langchain", "react"}

    score_bonus_skills = sorted(in_progress_skills.difference(profile_skills))
    return {
        "learning_progress": {
            "completed_skills": sorted(completed_skills),
            "in_progress_skills": sorted(in_progress_skills),
            "score_bonus_skills": score_bonus_skills,
        }
    }


def learning_plan_node(state: dict[str, Any]) -> dict[str, Any]:
    learning_progress = state.get("learning_progress", {})
    completed = set(learning_progress.get("completed_skills", []))
    in_progress = set(learning_progress.get("in_progress_skills", []))
    if not learning_progress:
        return {"learning_plan": "No learning progress found. Initialize learning tracker first."}

    approved = state.get("approved_jobs", [])
    candidate_jobs = approved or state.get("scored_jobs", [])[:2]
    missing_skills = set()
    profile_skills = set(state.get("profile", {}).get("skills", [])).union(completed)
    for job in candidate_jobs:
        for skill in job.get("skills", []):
            if skill not in profile_skills:
                missing_skills.add(skill)
    missing_skills = missing_skills.difference(in_progress)

    plan = (
        "7-day plan:\n"
        "1) Keep one project-based learning track independent of current applications.\n"
        "2) Build one mini project proving the top missing skill.\n"
        f"3) Missing skills focus: {', '.join(sorted(missing_skills)) or 'None'}.\n"
        f"4) In progress now: {', '.join(sorted(in_progress)) or 'None'}.\n"
        "5) Practice 10 interview questions and record answers.\n"
    )
    return {"learning_plan": plan}
