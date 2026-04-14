from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import streamlit as st

from jobpilot.stories import Story, StoryBank
from jobpilot.job_sources import load_pipeline_jobs, save_pipeline_jobs, merge_jobs

_WORK_DIR = Path("data/work")


def _work_path(job_id: str) -> Path:
    return _WORK_DIR / f"{job_id}.json"


def _load_work(job_id: str) -> dict | None:
    """Load saved working state for a job."""
    path = _work_path(job_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_work(job_id: str, work: dict) -> None:
    """Save working state for a job."""
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    _work_path(job_id).write_text(
        json.dumps(work, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _delete_work(job_id: str) -> None:
    """Delete saved working state for a job."""
    _work_path(job_id).unlink(missing_ok=True)


def get_bank() -> StoryBank:
    return StoryBank()


# --- Page config ---
st.set_page_config(page_title="JobPilot", page_icon="briefcase", layout="wide")
st.title("JobPilot")

tab_input, tab_stories, tab_pipeline, tab_tracking = st.tabs(
    ["Add Content", "Story Bank", "Job Pipeline", "Applications"]
)

# ============================================================
# TAB 1: Single Input
# ============================================================
with tab_input:
    st.header("Tell me about yourself")
    st.caption("Type anything or upload a file. Claude will figure out what it is — career history, project stories, CV — and store it in the right place.")

    col_text, col_upload = st.columns([3, 1])

    with col_upload:
        st.markdown("**Or upload a file**")
        uploaded = st.file_uploader("CV, resume, or text file", type=["txt", "pdf", "md", "doc", "docx"], label_visibility="collapsed")

    with col_text:
        input_text = st.text_area(
            "Your input:",
            height=250,
            placeholder=(
                "You can type anything here. For example:\n\n"
                "Career history:\n"
                "  2007-2011 Wuhan University, BSc Information Management\n"
                "  2011-2024 Shanghai Tax Bureau, tax officer\n\n"
                "Or a project story:\n"
                "  Built a RAG pipeline using LangChain and Pinecone,\n"
                "  cut response time from 900ms to 300ms\n\n"
                "Or paste CV bullet points, LinkedIn sections, anything."
            ),
        )

    # Handle file upload → extract text
    if uploaded is not None and not input_text.strip():
        if uploaded.type == "application/pdf":
            try:
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(uploaded.getvalue()))
                pages = [page.extract_text() or "" for page in reader.pages]
                input_text = "\n\n".join(pages).strip()
            except Exception as exc:
                st.error(f"Could not read PDF: {exc}")
                input_text = ""
        else:
            input_text = uploaded.getvalue().decode("utf-8", errors="replace")

        if input_text:
            st.text_area("Extracted from file:", input_text[:3000], height=150, disabled=True)

    if st.button("Process", type="primary", use_container_width=True) and input_text.strip():
        bank = get_bank()

        with st.spinner("Claude is reading your input..."):
            try:
                from jobpilot.llm import _call_claude, _parse_json_response

                classify_prompt = (
                    "Classify this text. What does it contain?\n"
                    "Return a JSON object with:\n"
                    "- has_stories: true if it contains project descriptions, achievements, or work experiences\n"
                    "- summary: one sentence describing what this text is\n\n"
                    f"Text:\n{input_text[:3000]}\n\n"
                    "Return ONLY valid JSON, no markdown fences."
                )
                classification = _parse_json_response(_call_claude(classify_prompt))
            except Exception:
                classification = {"has_stories": True, "summary": "text input"}

        results = {"story_count": 0, "skipped": 0}

        # Extract stories
        if classification.get("has_stories"):
            with st.spinner("Extracting stories..."):
                try:
                    from jobpilot.llm import import_stories
                    stories = import_stories(input_text[:5000])
                    added = bank.add_story_batch(stories)
                    results["story_count"] = len(added)
                    results["skipped"] = len(stories) - len(added)
                except Exception as exc:
                    st.warning(f"Story extraction failed: {exc}")

        # Show results
        parts = []
        if results["story_count"]:
            parts.append(f"{results['story_count']} stories")
        if results["skipped"]:
            parts.append(f"{results['skipped']} duplicates skipped")

        if parts:
            st.success(f"Added: {', '.join(parts)}")
        elif results["skipped"]:
            st.info("All content was already in your story bank (duplicates skipped).")
        else:
            st.warning("Could not extract any content. Try rephrasing or adding more detail.")

    # Quick stats
    bank = get_bank()
    st.divider()
    col1, col2, col3 = st.columns(3)
    col1.metric("Stories", len(bank.list_stories()))
    apps_path = Path("data/applications.json")
    app_count = len(json.loads(apps_path.read_text(encoding="utf-8"))) if apps_path.exists() else 0
    col3.metric("Applications", app_count)


# ============================================================
# TAB 2: Story Bank
# ============================================================
with tab_stories:
    st.header("Story Bank")

    bank = get_bank()
    stories = bank.list_stories()

    if not stories:
        st.info("No stories yet. Use the 'Add Content' tab to add stories.")
        if st.button("Migrate hardcoded stories (one-time setup)"):
            from jobpilot.storage import InMemoryStore
            legacy = InMemoryStore._get_legacy_stories()
            if legacy:
                try:
                    from jobpilot.llm import migrate_legacy_stories
                    with st.spinner("Claude is structuring your stories..."):
                        structured = migrate_legacy_stories(legacy)
                    for s in structured:
                        bank.add_story(s)
                    st.success(f"Migrated {len(structured)} stories!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Migration failed: {exc}")
    else:
        # Filter controls
        col_tag, col_skill = st.columns(2)
        with col_tag:
            all_tags = sorted({t for s in stories for t in s.tags})
            selected_tag = st.selectbox("Filter by tag", ["All"] + all_tags)
        with col_skill:
            all_skills = sorted({sk for s in stories for sk in s.skills})
            selected_skill = st.selectbox("Filter by skill", ["All"] + all_skills)

        filtered = bank.list_stories(
            tag=selected_tag if selected_tag != "All" else None,
            skill=selected_skill if selected_skill != "All" else None,
        )

        st.caption(f"{len(filtered)} stories")

        for story in filtered:
            with st.expander(f"{story.title}", expanded=False):
                st.markdown(f"**Situation:** {story.situation}")
                st.markdown(f"**Action:** {story.action}")
                st.markdown(f"**Result:** {story.result}")
                st.markdown(f"**Tags:** {', '.join(story.tags)}")
                st.markdown(f"**Skills:** {', '.join(story.skills)}")
                st.caption(f"ID: {story.id} | Source: {story.source} | Added: {story.date_added}")

                col_refine, col_del = st.columns([3, 1])
                with col_refine:
                    correction = st.text_input("Refine:", key=f"refine_input_{story.id}", placeholder="e.g. 'it was 40% not 3x'")
                    if st.button("Apply", key=f"refine_{story.id}") and correction:
                        try:
                            from jobpilot.llm import refine_story
                            with st.spinner("Refining..."):
                                refined = refine_story(story, correction)
                            bank.update_story(refined)
                            st.success("Refined!")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Refine failed: {exc}")
                with col_del:
                    if st.button("Delete", key=f"del_{story.id}"):
                        bank.delete_story(story.id)
                        st.rerun()


# ============================================================
# TAB 3: Job Pipeline
# ============================================================
with tab_pipeline:
    st.header("Job Search Pipeline")

    profile_path = Path("data/profile.json")
    master_cv_path = Path("data/master_cv.json")
    if not profile_path.exists():
        st.warning("No profile found. Run `jobpilot init-profile` first, or create data/profile.json.")
    elif not master_cv_path.exists():
        st.warning("No master CV found. Create data/master_cv.json with your career data.")
    else:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        master_cv = json.loads(master_cv_path.read_text(encoding="utf-8"))
        contact = master_cv.get("contact", {})
        search_config = json.loads(profile_path.read_text(encoding="utf-8"))
        st.caption(f"{contact.get('name', '')} | Searching: {', '.join(search_config.get('target_roles', [])[:4])}")

    bank = get_bank()
    story_count = len(bank.list_stories())
    st.caption(f"Story bank: {story_count} stories")

    if story_count == 0:
        st.warning("Add some stories first! The pipeline needs stories to tailor your CV.")

    # Load persisted jobs into session state on first visit
    if "found_jobs" not in st.session_state:
        persisted = load_pipeline_jobs()
        if persisted:
            st.session_state["found_jobs"] = persisted

    st.subheader("Search & Score")
    custom_query = st.text_input("Custom search query (optional):", placeholder="Leave blank to use profile target roles")

    if st.button("Search Jobs"):
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))

            if custom_query.strip():
                query_candidates = [custom_query.strip()]
            else:
                query_candidates = [r for r in profile.get("target_roles", []) if r][:4]

            location = profile.get("location", "Dublin, Ireland")

            with st.spinner("Searching the web for jobs (this may take a minute)..."):
                from jobpilot.llm import search_jobs_web
                from jobpilot.job_sources import _apply_profile_filters
                jobs = search_jobs_web(query_candidates, location=location)
                jobs = _apply_profile_filters(jobs, profile, limit=15)
                today = date.today().isoformat()
                for job in jobs:
                    if "date_found" not in job:
                        job["date_found"] = today

            # Merge new results with existing persisted jobs
            existing = load_pipeline_jobs()
            merged = merge_jobs(existing, jobs)
            save_pipeline_jobs(merged)

            query = ", ".join(query_candidates)
            st.session_state["found_jobs"] = merged
            st.session_state["search_query"] = query
            new_count = len(merged) - len(existing)
            st.success(f"Found {len(jobs)} jobs ({new_count} new, {len(merged)} total) | queries: {query}")
        except Exception as exc:
            st.error(f"Search failed: {exc}")

    if "found_jobs" in st.session_state and st.session_state["found_jobs"]:
        jobs = st.session_state["found_jobs"]

        # Score jobs
        from jobpilot.profile import load_profile
        from jobpilot.config import load_settings
        from jobpilot.job_sources import score_jobs
        profile = load_profile(load_settings())
        profile_skills = set(profile.get("skills", []))
        scored_jobs = score_jobs(jobs, profile_skills)

        # If working on a specific job, show that workflow
        if "working_job" in st.session_state:
            job = st.session_state["working_job"]
            st.subheader(f"Working on: {job['title']} at {job['company']}")

            # Load saved work from disk if not in session
            saved = _load_work(job["id"])
            if saved and "working_cv" not in st.session_state:
                st.session_state["working_cv"] = saved.get("cv_data")
                st.session_state["working_cl"] = saved.get("cover_letter")
                st.session_state["working_role_level"] = saved.get("role_level")
                if saved.get("evaluation"):
                    st.session_state["working_eval"] = saved["evaluation"]
                if saved.get("iterations"):
                    st.session_state["working_iterations"] = saved["iterations"]
                if saved.get("job"):
                    st.session_state["working_job"] = saved["job"]
                    job = saved["job"]

            col_back, col_start_over = st.columns(2)
            with col_back:
                if st.button("Back to job list"):
                    for key in ["working_job", "working_cv", "working_cl", "working_eval", "working_role_level", "working_iterations"]:
                        st.session_state.pop(key, None)
                    st.rerun()
            with col_start_over:
                if st.button("Start over (discard progress)"):
                    _delete_work(job["id"])
                    for key in ["working_cv", "working_cl", "working_eval", "working_role_level", "working_iterations"]:
                        st.session_state.pop(key, None)
                    st.rerun()

            # Step 1: Fetch JD + Classify + Auto-tailor loop
            if "working_cv" not in st.session_state:
                if st.button("Start tailoring (auto-loop)", type="primary"):
                    with st.spinner("Fetching full job description..."):
                        try:
                            from jobpilot.llm import fetch_full_jd
                            if job.get("url") and len(job.get("description", "")) < 200:
                                full_jd = fetch_full_jd(job.get("url", ""))
                                if full_jd:
                                    job["full_description"] = full_jd
                                    st.session_state["working_job"] = job
                        except Exception:
                            pass

                    with st.spinner("Classifying role level..."):
                        from jobpilot.llm import classify_role_level
                        role_level = classify_role_level(job)
                        st.session_state["working_role_level"] = role_level

                    try:
                        from jobpilot.llm import auto_tailor_loop
                        job_text = job.get("full_description", job.get("description", ""))
                        relevant_stories = bank.find_similar(job_text, top_k=8)

                        with st.status(f"Auto-tailoring (role level: {role_level})...", expanded=True) as status:
                            def _progress(msg: str) -> None:
                                status.write(msg)

                            result = auto_tailor_loop(
                                job, relevant_stories, profile,
                                role_level=role_level,
                                max_iterations=3,
                                progress_cb=_progress,
                            )
                            status.update(label="Auto-tailoring complete", state="complete", expanded=False)

                        cv_data = result["cv"]
                        cv_data["role_level"] = role_level
                        cover_letter = result["cover_letter"]
                        evaluation = result["evaluation"]
                        iterations = result["iterations"]

                        st.session_state["working_cv"] = cv_data
                        st.session_state["working_cl"] = cover_letter
                        st.session_state["working_eval"] = evaluation
                        st.session_state["working_iterations"] = iterations
                        # Save to disk
                        _save_work(job["id"], {
                            "job": job, "cv_data": cv_data, "cover_letter": cover_letter,
                            "role_level": role_level, "evaluation": evaluation,
                            "iterations": iterations, "stage": "auto_tailored",
                        })
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Auto-tailoring failed: {exc}")
            else:
                cv_data = st.session_state["working_cv"]
                cover_letter = st.session_state["working_cl"]
                role_level = st.session_state.get("working_role_level", "unknown")

                # Step 2: Review tailored content
                st.info(f"Role level: **{role_level}**")

                st.markdown("**Professional Summary:**")
                st.write(cv_data.get("summary", ""))

                st.markdown("**Experience:**")
                for exp in cv_data.get("experience", []):
                    st.markdown(f"*{exp.get('title', '')}* — {exp.get('company', '')} ({exp.get('dates', '')})")
                    for bullet in exp.get("bullets", []):
                        st.markdown(f"  - {bullet}")

                if cv_data.get("education"):
                    st.markdown("**Education:**")
                    for edu in cv_data.get("education", []):
                        st.markdown(f"*{edu.get('degree', '')}* — {edu.get('institution', '')} ({edu.get('dates', '')})")
                        for detail in edu.get("details", []):
                            st.markdown(f"  - {detail}")

                if cv_data.get("projects"):
                    st.markdown("**Projects:**")
                    for proj in cv_data.get("projects", []):
                        st.markdown(f"*{proj.get('title', '')}* ({proj.get('tech', '')})")
                        for bullet in proj.get("bullets", []):
                            st.markdown(f"  - {bullet}")

                st.markdown("**Skills:**")
                skills = cv_data.get("skills", {})
                if isinstance(skills, dict):
                    for category, items in skills.items():
                        st.markdown(f"  **{category}:** {', '.join(items)}")
                else:
                    st.markdown(f"  {', '.join(skills)}")

                st.divider()
                st.markdown("**Cover Letter:**")
                st.write(cover_letter)

                # Step 3: Evaluation results (from auto-loop)
                st.divider()
                evaluation = st.session_state.get("working_eval")
                iterations = st.session_state.get("working_iterations", [])

                if evaluation:
                    st.subheader("Evaluation Results")

                    col_score, col_shortlist = st.columns(2)
                    col_score.metric("Score", f"{evaluation.get('overall_score', '?')}/10")
                    col_shortlist.metric("Would Shortlist", "Yes" if evaluation.get("would_shortlist") else "No")

                    if iterations:
                        st.markdown("**Auto-loop iterations:**")
                        for it in iterations:
                            check = "[shortlist]" if it.get("shortlist") else ""
                            st.markdown(f"  - Iteration {it.get('iter')}: score {it.get('score', '?')}/10 {check}")

                    if evaluation.get("red_flags"):
                        st.warning("**Red Flags:** " + " | ".join(evaluation["red_flags"]))

                    kw = evaluation.get("keyword_coverage", {})
                    if kw.get("missing_critical"):
                        st.error("**Missing critical keywords:** " + ", ".join(kw["missing_critical"]))
                    if kw.get("matched"):
                        st.success("**Matched keywords:** " + ", ".join(kw["matched"][:10]))

                    if evaluation.get("suggestions"):
                        st.info("**Suggestions:**\n" + "\n".join(f"- {s}" for s in evaluation["suggestions"]))

                    if evaluation.get("strengths"):
                        st.success("**Strengths:** " + " | ".join(evaluation["strengths"]))

                    # Gaps analysis
                    gaps = evaluation.get("gaps", {}) or {}
                    quick_fill = gaps.get("quick_fill", []) or []
                    hard_gaps = gaps.get("hard_gaps", []) or []

                    if quick_fill:
                        st.divider()
                        st.subheader("Quick-fill gaps")
                        st.caption(
                            "These skills can be acquired in a few hours. Complete the exercise, "
                            "add a new story in the **Add Content** tab, then click **Start over** "
                            "to re-run the auto-loop with the new story included."
                        )
                        for gap in quick_fill:
                            with st.container(border=True):
                                st.markdown(f"**{gap.get('skill', '?')}**")
                                if gap.get("reason_missing"):
                                    st.caption(f"Why missing: {gap['reason_missing']}")
                                if gap.get("how_to_fill"):
                                    st.markdown(f"**How to fill:** {gap['how_to_fill']}")
                                if gap.get("suggested_bullet"):
                                    st.markdown(f"**Suggested bullet (after completing):** _{gap['suggested_bullet']}_")

                    if hard_gaps:
                        st.divider()
                        st.subheader("Hard gaps")
                        st.info(
                            "These can't be quickly filled. Consider whether this role is "
                            "worth pursuing despite the gap."
                        )
                        for hg in hard_gaps:
                            st.markdown(f"- {hg}")

                # Step 4: Manual feedback (fine-tuning after auto-loop)
                st.divider()
                st.markdown("**Manual fine-tuning**")
                st.caption("Optional — only use if the auto-loop result needs specific tweaks.")
                with st.form("cv_feedback_form", clear_on_submit=True):
                    feedback = st.text_input(
                        "Give feedback on the CV or cover letter:",
                        placeholder="e.g. 'make summary shorter', 'drop tax bureau bullets', 'emphasize NLP more'",
                    )
                    submitted = st.form_submit_button("Apply feedback")
                if submitted and feedback.strip():
                    with st.spinner("Revising based on your feedback..."):
                        try:
                            from jobpilot.llm import revise_cv
                            revised_cv, revised_cl = revise_cv(cv_data, cover_letter, job, feedback.strip())
                            st.session_state["working_cv"] = revised_cv
                            st.session_state["working_cl"] = revised_cl
                            st.session_state.pop("working_eval", None)
                            _save_work(job["id"], {
                                "job": job, "cv_data": revised_cv, "cover_letter": revised_cl,
                                "role_level": role_level, "stage": "revised",
                            })
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Revision failed: {exc}")

                # Re-evaluate (only shown if manual feedback cleared the eval)
                if not evaluation:
                    if st.button("Re-evaluate"):
                        with st.spinner("Running independent CV evaluation..."):
                            try:
                                from jobpilot.llm import evaluate_cv
                                new_eval = evaluate_cv(cv_data, job, cover_letter)
                                st.session_state["working_eval"] = new_eval
                                _save_work(job["id"], {
                                    "job": job, "cv_data": cv_data, "cover_letter": cover_letter,
                                    "role_level": role_level, "evaluation": new_eval,
                                    "iterations": iterations, "stage": "re_evaluated",
                                })
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Evaluation failed: {exc}")

                # Step 5: Generate PDFs
                st.divider()
                col_pdf, col_retailor = st.columns(2)
                with col_retailor:
                    if st.button("Re-tailor"):
                        _delete_work(job["id"])
                        for key in ["working_cv", "working_cl", "working_eval", "working_iterations"]:
                            st.session_state.pop(key, None)
                        st.rerun()
                with col_pdf:
                    if st.button("Generate PDFs", type="primary"):
                        try:
                            from jobpilot.renderer import render_cv, render_cover_letter
                            output_dir = Path("output") / job["id"]
                            output_dir.mkdir(parents=True, exist_ok=True)

                            with st.spinner("Rendering PDFs..."):
                                cv_path = render_cv(cv_data, output_dir / "cv.pdf")
                                cl_render_data = {
                                    "name": cv_data.get("name", ""),
                                    "date": date.today().strftime("%B %d, %Y"),
                                    "company": job.get("company", ""),
                                    "job_title": job.get("title", ""),
                                    "body": cover_letter,
                                }
                                cl_path = render_cover_letter(cl_render_data, output_dir / "cover_letter.pdf")

                            st.success(f"PDFs saved to {output_dir}/")
                            col_dl1, col_dl2 = st.columns(2)
                            with col_dl1:
                                with open(cv_path, "rb") as f:
                                    st.download_button("Download CV", f.read(), file_name=f"cv_{job['company']}.pdf", mime="application/pdf")
                            with col_dl2:
                                with open(cl_path, "rb") as f:
                                    st.download_button("Download Cover Letter", f.read(), file_name=f"cover_letter_{job['company']}.pdf", mime="application/pdf")

                            # Clear working state after PDF generation
                            if st.button("Done — next job"):
                                for key in ["working_job", "working_cv", "working_cl", "working_eval", "working_role_level"]:
                                    st.session_state.pop(key, None)
                                st.rerun()
                        except Exception as exc:
                            st.error(f"PDF generation failed: {exc}")

        else:
            # Job list — click one to work on it
            st.subheader(f"Found Jobs ({len(scored_jobs)})")
            for job in scored_jobs:
                saved = _load_work(job["id"])
                stage = saved.get("stage", "") if saved else ""
                badge = {"tailored": "CV ready", "revised": "CV revised", "evaluated": "Evaluated"}.get(stage, "")

                col_btn, col_info = st.columns([2, 8])
                with col_btn:
                    label = "Continue" if saved else "Work on this"
                    if st.button(label, key=f"select_{job['id']}"):
                        st.session_state["working_job"] = job
                        st.rerun()
                with col_info:
                    title_line = f"**{job['title']}** at {job['company']} (score: {job['score']})"
                    if badge:
                        title_line += f"  —  *{badge}*"
                    st.markdown(title_line)
                    date_found = job.get("date_found", "")
                    date_label = f" | found: {date_found}" if date_found else ""
                    st.caption(f"{job.get('location', '')} | {job.get('source', '')} | {job.get('posted', '')}{date_label}")
                    if job.get("url"):
                        st.caption(f"[Link]({job['url']})")


# ============================================================
# TAB 5: Application Tracking
# ============================================================
with tab_tracking:
    st.header("Applications")

    apps_path = Path("data/applications.json")
    if not apps_path.exists() or not json.loads(apps_path.read_text(encoding="utf-8")):
        st.info("No applications tracked yet. Use the Job Pipeline tab to generate your first application.")
    else:
        apps = json.loads(apps_path.read_text(encoding="utf-8"))

        statuses = [a.get("status", "unknown") for a in apps]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total", len(apps))
        col2.metric("Tailored", statuses.count("tailored"))
        col3.metric("Submitted", statuses.count("submitted"))
        col4.metric("Interview", statuses.count("interview"))

        st.divider()

        for app in apps:
            col_info, col_status = st.columns([4, 1])
            with col_info:
                st.markdown(f"**{app.get('title', '?')}** at {app.get('company', '?')}")
                st.caption(f"ID: {app.get('job_id', '?')} | Discovered: {app.get('date_discovered', '?')}")
            with col_status:
                new_status = st.selectbox(
                    "Status",
                    ["discovered", "reviewed", "tailored", "submitted", "interview", "rejected", "offer"],
                    index=["discovered", "reviewed", "tailored", "submitted", "interview", "rejected", "offer"].index(app.get("status", "discovered")),
                    key=f"status_{app.get('job_id', '')}",
                )
                if new_status != app.get("status"):
                    app["status"] = new_status
                    apps_path.write_text(
                        json.dumps(apps, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    st.rerun()
