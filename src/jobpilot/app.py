from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import streamlit as st

from jobpilot.stories import Story, StoryBank
from jobpilot.job_sources import load_pipeline_jobs, save_pipeline_jobs, merge_jobs

_WORK_DIR = Path("data/work")

# Floating back-to-top button
st.markdown("""
<style>
.back-to-top {
    position: fixed;
    bottom: 30px;
    right: 30px;
    z-index: 999;
    background-color: #262730;
    color: white;
    border: 1px solid #4a4a5a;
    border-radius: 50%;
    width: 40px;
    height: 40px;
    font-size: 20px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    text-decoration: none;
}
.back-to-top:hover { background-color: #3a3a4a; color: white; }
</style>
<a class="back-to-top" href="javascript:void(0)" onclick="window.parent.document.querySelector('section.main').scrollTo({top: 0, behavior: 'smooth'})" title="Back to top">↑</a>
""", unsafe_allow_html=True)


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

# --- Custom CSS ---
st.markdown("""
<style>
/* Score badges */
.score-high { background: #1a7a3a; color: white; padding: 2px 10px; border-radius: 12px; font-weight: 600; font-size: 0.85em; }
.score-mid { background: #b8860b; color: white; padding: 2px 10px; border-radius: 12px; font-weight: 600; font-size: 0.85em; }
.score-low { background: #6b7280; color: white; padding: 2px 10px; border-radius: 12px; font-weight: 600; font-size: 0.85em; }
.badge-applied { background: #2563eb; color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.8em; }
.badge-ready { background: #7c3aed; color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.8em; }
.badge-new { background: #059669; color: white; padding: 2px 10px; border-radius: 12px; font-size: 0.8em; }

/* Job cards */
.job-card {
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 10px;
    transition: border-color 0.2s;
}
.job-card:hover { border-color: #6366f1; }
.job-card h4 { margin: 0 0 4px 0; font-size: 1.05em; }
.job-card .meta { color: #6b7280; font-size: 0.85em; margin-top: 4px; }

/* Metric cards */
.metric-card {
    background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    padding: 16px 20px;
    text-align: center;
}
.metric-card .value { font-size: 2em; font-weight: 700; color: #1e293b; }
.metric-card .label { font-size: 0.85em; color: #64748b; margin-top: 2px; }

/* Step indicator */
.step-active { color: #6366f1; font-weight: 700; }
.step-done { color: #22c55e; }
.step-pending { color: #94a3b8; }

/* Tighter spacing */
.block-container { padding-top: 1.5rem; }
div[data-testid="stExpander"] { border: 1px solid #e5e7eb; border-radius: 8px; margin-bottom: 8px; }
</style>
""", unsafe_allow_html=True)


def _score_badge(score: float) -> str:
    """Return HTML score badge with color."""
    pct = int(score * 100)
    if score >= 0.6:
        return f'<span class="score-high">{pct}%</span>'
    elif score >= 0.3:
        return f'<span class="score-mid">{pct}%</span>'
    return f'<span class="score-low">{pct}%</span>'


def _status_badge(text: str, kind: str = "ready") -> str:
    return f'<span class="badge-{kind}">{text}</span>'


# --- Sidebar ---
with st.sidebar:
    st.markdown("### JobPilot")
    bank_sidebar = get_bank()
    story_count_sidebar = len(bank_sidebar.list_stories())

    apps_path_sidebar = Path("data/applications.json")
    app_list_sidebar = []
    if apps_path_sidebar.exists():
        try:
            app_list_sidebar = json.loads(apps_path_sidebar.read_text(encoding="utf-8"))
        except Exception:
            pass

    pipeline_sidebar = load_pipeline_jobs()

    st.markdown(f"""
    <div class="metric-card"><div class="value">{len(pipeline_sidebar)}</div><div class="label">Jobs in Pipeline</div></div>
    """, unsafe_allow_html=True)
    st.markdown("")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        st.markdown(f"""
        <div class="metric-card"><div class="value">{story_count_sidebar}</div><div class="label">Stories</div></div>
        """, unsafe_allow_html=True)
    with col_s2:
        st.markdown(f"""
        <div class="metric-card"><div class="value">{len(app_list_sidebar)}</div><div class="label">Applied</div></div>
        """, unsafe_allow_html=True)

    st.divider()
    profile_path_sidebar = Path("data/profile.json")
    if profile_path_sidebar.exists():
        p = json.loads(profile_path_sidebar.read_text(encoding="utf-8"))
        st.caption(f"Target: {', '.join(p.get('target_roles', [])[:3])}")
        st.caption(f"Location: {', '.join(p.get('locations', []))}")

st.title("JobPilot")

tab_input, tab_stories, tab_pipeline, tab_tracking, tab_gaps = st.tabs(
    ["Add Content", "Story Bank", "Job Pipeline", "Applications", "Skills Gaps"]
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

    if story_count == 0:
        st.warning("Add some stories first! The pipeline needs stories to tailor your CV.")

    # Load persisted jobs into session state on first visit
    if "found_jobs" not in st.session_state:
        persisted = load_pipeline_jobs()
        if persisted:
            from jobpilot.job_sources import enrich_jobs_missing_skills
            from jobpilot.profile import load_profile as _lp
            from jobpilot.config import load_settings as _ls
            persisted = enrich_jobs_missing_skills(persisted, _lp(_ls()))
            st.session_state["found_jobs"] = persisted

    tab_add_job, tab_search = st.tabs(["Add Job Manually", "Web Search"])

    with tab_add_job:
        st.caption("Paste a job URL and description directly — no search needed.")
        col_title, col_company = st.columns(2)
        with col_title:
            manual_title = st.text_input("Job title:", placeholder="e.g. Junior AI/ML Agent Engineer")
        with col_company:
            manual_company = st.text_input("Company:", placeholder="e.g. eBay")
        manual_url = st.text_input("Job URL (optional):", placeholder="https://...")
        manual_jd = st.text_area("Job description:", height=200, placeholder="Paste the full job description here...")

        if st.button("Add to pipeline", type="primary") and manual_title.strip() and manual_jd.strip():
            import hashlib
            today = date.today().isoformat()
            job_id = f"manual_{hashlib.md5(f'{manual_title}{manual_company}'.encode()).hexdigest()[:8]}"
            from jobpilot.job_sources import _extract_skills_from_text
            from jobpilot.profile import load_profile
            from jobpilot.config import load_settings
            prof = load_profile(load_settings())
            profile_candidates = prof.get("skills", []) + prof.get("preferred_keywords", [])

            new_job = {
                "id": job_id,
                "title": manual_title.strip(),
                "company": manual_company.strip(),
                "location": prof.get("location", ""),
                "description": manual_jd.strip()[:2000],
                "full_description": manual_jd.strip(),
                "url": manual_url.strip(),
                "source": "manual",
                "posted": today,
                "date_found": today,
                "skills": _extract_skills_from_text(f"{manual_title} {manual_jd}", profile_candidates),
            }
            existing = load_pipeline_jobs()
            merged = merge_jobs(existing, [new_job])
            save_pipeline_jobs(merged)
            st.session_state["found_jobs"] = merged
            st.success(f"Added: {manual_title} at {manual_company}")
            st.rerun()

    with tab_search:
        st.caption("Search the web for jobs using Claude (slower, broader).")
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
        from jobpilot.referrals import load_connections
        _connections = load_connections(load_settings().connections_csv)
        scored_jobs = score_jobs(
            jobs, profile_skills,
            preferred_keywords=profile.get("preferred_keywords"),
            target_roles=profile.get("target_roles"),
            connections=_connections,
        )

        # Sort options
        sort_by = st.selectbox("Sort by", ["Score (high to low)", "Posted (newest)", "Date found (newest)"])
        if sort_by == "Posted (newest)":
            scored_jobs.sort(key=lambda x: x.get("posted", ""), reverse=True)
        elif sort_by == "Date found (newest)":
            scored_jobs.sort(key=lambda x: x.get("date_found", ""), reverse=True)
        # else: already sorted by score from score_jobs()

        # If working on a specific job, show that workflow
        if "working_job" in st.session_state:
            job = st.session_state["working_job"]
            st.subheader(f"Working on: {job['title']} at {job['company']}")

            # Referral hint — ask for a referral BEFORE applying (10x lever, book ch.2).
            try:
                from jobpilot.config import load_settings as _ls_ref
                from jobpilot.referrals import find_referrers, load_connections

                _conns = load_connections(_ls_ref().connections_csv)
                refs = find_referrers(job.get("company", ""), _conns) if _conns else []
                if refs:
                    names = ", ".join(
                        r.name + (f" ({r.position})" if r.position else "") for r in refs[:6]
                    )
                    more = f" — and {len(refs) - 6} more" if len(refs) > 6 else ""
                    st.success(
                        f"🤝 **{len(refs)} connection(s) at {job.get('company', '')}** — "
                        f"ask for a referral *before* applying: {names}{more}"
                    )
            except Exception:
                pass

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
                            from jobpilot.llm import fetch_full_jd, summarize_jd
                            if job.get("url") and len(job.get("description", "")) < 200:
                                full_jd = fetch_full_jd(job.get("url", ""))
                                if full_jd:
                                    job["full_description"] = full_jd
                                    st.session_state["working_job"] = job
                            # Generate summary if missing
                            jd_text = job.get("full_description", "") or job.get("description", "")
                            if jd_text and not job.get("jd_summary"):
                                job["jd_summary"] = summarize_jd(jd_text, job.get("title", ""), job.get("company", ""))
                                # Persist summary to pipeline
                                _pipeline = load_pipeline_jobs()
                                for pj in _pipeline:
                                    if pj.get("id") == job.get("id"):
                                        pj["jd_summary"] = job["jd_summary"]
                                        if job.get("full_description"):
                                            pj["full_description"] = job["full_description"]
                                        break
                                save_pipeline_jobs(_pipeline)
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
                        # Send Telegram notification
                        try:
                            from jobpilot.notify import send_telegram
                            _score = evaluation.get("overall_score", "?")
                            _sl = "Yes" if evaluation.get("would_shortlist") else "No"
                            send_telegram(
                                f"JobPilot: Tailoring complete\n"
                                f"*{job.get('title', '')}* at {job.get('company', '')}\n"
                                f"Score: {_score}/10 | Shortlist: {_sl}"
                            )
                        except Exception:
                            pass
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Auto-tailoring failed: {exc}")
            else:
                cv_data = st.session_state["working_cv"]
                cover_letter = st.session_state["working_cl"]
                role_level = st.session_state.get("working_role_level", "unknown")

                if not cv_data:
                    st.error("CV data is empty. Try clicking 'Start over' and re-running.")
                    if st.button("Start over"):
                        _delete_work(job["id"])
                        for key in ["working_cv", "working_cl", "working_eval", "working_role_level", "working_iterations"]:
                            st.session_state.pop(key, None)
                        st.rerun()
                else:
                    pass  # fall through to display below

                # Step indicator
                eval_exists = st.session_state.get("working_eval") is not None
                _apps_check = Path("data/applications.json")
                _applied_ids = set()
                if _apps_check.exists():
                    try:
                        _applied_ids = {a.get("job_id") for a in json.loads(_apps_check.read_text(encoding="utf-8"))}
                    except Exception:
                        pass
                steps = [
                    ("Tailor", True),
                    ("Evaluate", eval_exists),
                    ("PDFs", (Path("output") / job["id"] / "cv.pdf").exists()),
                    ("Apply", job["id"] in _applied_ids),
                ]
                step_html = " &rarr; ".join(
                    f'<span class="step-done">{name}</span>' if done else f'<span class="step-pending">{name}</span>'
                    for name, done in steps
                )
                st.markdown(f"<div style='margin-bottom:12px;font-size:0.95em;'>{step_html}</div>", unsafe_allow_html=True)
                st.caption(f"Role level: **{role_level}**")

                # Collapsible CV preview
                with st.expander("Tailored CV", expanded=True):
                    st.markdown(f"**Summary:** {cv_data.get('summary', '')}")

                    st.markdown("**Experience**")
                    for exp in cv_data.get("experience", []):
                        st.markdown(f"*{exp.get('title', '')}* — {exp.get('company', '')} ({exp.get('dates', '')})")
                        for bullet in exp.get("bullets", []):
                            st.markdown(f"  - {bullet}")

                    if cv_data.get("education"):
                        st.markdown("**Education**")
                        for edu in cv_data.get("education", []):
                            st.markdown(f"*{edu.get('degree', '')}* — {edu.get('institution', '')} ({edu.get('dates', '')})")
                            for detail in edu.get("details", []):
                                st.markdown(f"  - {detail}")

                    if cv_data.get("projects"):
                        st.markdown("**Projects**")
                        for proj in cv_data.get("projects", []):
                            st.markdown(f"*{proj.get('title', '')}* ({proj.get('tech', '')})")
                            for bullet in proj.get("bullets", []):
                                st.markdown(f"  - {bullet}")

                    st.markdown("**Skills**")
                    skills = cv_data.get("skills", {})
                    if isinstance(skills, dict):
                        for category, items in skills.items():
                            st.markdown(f"  **{category}:** {', '.join(items)}")
                    else:
                        st.markdown(f"  {', '.join(skills)}")

                with st.expander("Cover Letter", expanded=False):
                    st.write(cover_letter)

                # Step 3: Evaluation results (from auto-loop)
                st.divider()
                evaluation = st.session_state.get("working_eval")
                iterations = st.session_state.get("working_iterations", [])

                if evaluation:
                    st.subheader("Evaluation Results")

                    score_val = evaluation.get("overall_score", 0)
                    shortlisted = evaluation.get("would_shortlist", False)

                    # Score + ATS + shortlist in colored cards
                    ats_block = evaluation.get("ats_score") or {}
                    ats_overall = ats_block.get("overall")
                    col_score, col_ats, col_shortlist, col_exp_fit = st.columns(4)
                    with col_score:
                        score_color = "#16a34a" if score_val >= 7 else "#ca8a04" if score_val >= 5 else "#dc2626"
                        st.markdown(f'<div class="metric-card"><div class="value" style="color:{score_color}">{score_val}/10</div><div class="label">Recruiter Score</div></div>', unsafe_allow_html=True)
                    with col_ats:
                        if ats_overall is not None:
                            ats_color = "#16a34a" if ats_overall >= 0.75 else "#ca8a04" if ats_overall >= 0.5 else "#dc2626"
                            st.markdown(f'<div class="metric-card"><div class="value" style="color:{ats_color}">{ats_overall:.2f}</div><div class="label">ATS Score</div></div>', unsafe_allow_html=True)
                        else:
                            st.markdown('<div class="metric-card"><div class="value">—</div><div class="label">ATS Score</div></div>', unsafe_allow_html=True)
                    with col_shortlist:
                        sl_color = "#16a34a" if shortlisted else "#dc2626"
                        sl_text = "Yes" if shortlisted else "No"
                        st.markdown(f'<div class="metric-card"><div class="value" style="color:{sl_color}">{sl_text}</div><div class="label">Would Shortlist</div></div>', unsafe_allow_html=True)
                    with col_exp_fit:
                        exp_fit = evaluation.get("experience_fit", "?")
                        if isinstance(exp_fit, dict):
                            exp_score = exp_fit.get("score", exp_fit)
                        else:
                            exp_score = exp_fit
                        st.markdown(f'<div class="metric-card"><div class="value">{exp_score}</div><div class="label">Experience Fit</div></div>', unsafe_allow_html=True)

                    # Recruiter first-scan triage + career trajectory (book: ch.1 Yes/Maybe/No)
                    pile = evaluation.get("pile")
                    trajectory = evaluation.get("trajectory") or {}
                    badges = []
                    if pile:
                        pile_color = {"yes": "#16a34a", "maybe": "#ca8a04", "no": "#dc2626"}.get(str(pile).lower(), "#6b7280")
                        badges.append(f'<span style="color:{pile_color};font-weight:600">Pile: {str(pile).upper()}</span>')
                    if isinstance(trajectory, dict) and trajectory.get("assessment"):
                        traj_color = "#16a34a" if trajectory["assessment"] == "progression" else "#ca8a04"
                        note = trajectory.get("note", "")
                        badges.append(
                            f'<span style="color:{traj_color};font-weight:600">Trajectory: {trajectory["assessment"]}</span>'
                            + (f' — {note}' if note else "")
                        )
                    if badges:
                        st.markdown(" &nbsp;|&nbsp; ".join(badges), unsafe_allow_html=True)

                    if iterations:
                        def _fmt_iter(it: dict) -> str:
                            # New shape: {"iter", "ats_score", "llm_score", "shortlist"}
                            # Old shape: {"iter", "score", "shortlist"}
                            llm = it.get("llm_score", it.get("score", "?"))
                            ats = it.get("ats_score")
                            if ats is not None:
                                return f"llm {llm}/10 · ats {ats:.2f}"
                            return f"{llm}/10"
                        iter_text = "  →  ".join(_fmt_iter(it) for it in iterations)
                        st.caption(f"Auto-loop: {iter_text}")

                    # Surface ATS missing must-haves prominently — the concrete fixable gaps.
                    ats_coverage = ats_block.get("coverage") or {}
                    missing_must = ats_coverage.get("missing_must") or []
                    if missing_must:
                        st.warning(
                            f"**ATS missing must-haves ({len(missing_must)}):** "
                            + ", ".join(missing_must[:12])
                            + (f" … +{len(missing_must) - 12} more" if len(missing_must) > 12 else "")
                        )

                    # Keywords
                    kw = evaluation.get("keyword_coverage", {})
                    with st.expander("Keyword Coverage", expanded=False):
                        if kw.get("matched"):
                            st.success("**Matched:** " + ", ".join(kw["matched"][:15]))
                        if kw.get("missing_critical"):
                            st.error("**Missing (critical):** " + ", ".join(kw["missing_critical"]))
                        if kw.get("missing_nice_to_have"):
                            st.warning("**Missing (nice to have):** " + ", ".join(kw["missing_nice_to_have"][:10]))

                    # Strengths & issues side by side
                    col_good, col_bad = st.columns(2)
                    with col_good:
                        if evaluation.get("strengths"):
                            with st.expander("Strengths", expanded=True):
                                for s in evaluation["strengths"]:
                                    st.markdown(f"- {s}")
                    with col_bad:
                        issues = []
                        if evaluation.get("red_flags"):
                            issues.extend(f"**Red flag:** {rf}" for rf in evaluation["red_flags"])
                        if evaluation.get("ats_issues"):
                            issues.extend(f"**ATS:** {ai}" for ai in evaluation["ats_issues"])
                        if issues:
                            with st.expander("Issues", expanded=True):
                                for issue in issues:
                                    st.markdown(f"- {issue}")

                    if evaluation.get("suggestions"):
                        with st.expander("Suggestions", expanded=False):
                            for s in evaluation["suggestions"]:
                                st.markdown(f"- {s}")

                    # Weak bullets — passive voice / missing numbers / generic phrasing
                    weak_bullets = evaluation.get("weak_bullets") or []
                    if weak_bullets:
                        _issue_label = {
                            "passive_voice": "passive/weak verb",
                            "no_number": "no number",
                            "generic": "generic phrase",
                        }
                        with st.expander(f"Weak Bullets ({len(weak_bullets)})", expanded=False):
                            for wb in weak_bullets:
                                if isinstance(wb, dict):
                                    label = _issue_label.get(wb.get("issue", ""), wb.get("issue", ""))
                                    st.markdown(f"- _{label}_ — {wb.get('bullet', '')}")
                                else:
                                    st.markdown(f"- {wb}")

                    # Gaps analysis
                    gaps = evaluation.get("gaps", {}) or {}
                    quick_fill = gaps.get("quick_fill", []) or []
                    hard_gaps = gaps.get("hard_gaps", []) or []

                    if quick_fill:
                        st.divider()
                        st.subheader("Quick-fill gaps")
                        st.caption("These skills can be acquired in a few hours. Use the button to add a story and re-tailor.")
                        for gap_idx, gap in enumerate(quick_fill):
                            with st.container(border=True):
                                st.markdown(f"**{gap.get('skill', '?')}**")
                                if gap.get("reason_missing"):
                                    st.caption(f"Why missing: {gap['reason_missing']}")
                                if gap.get("how_to_fill"):
                                    with st.expander("How to fill"):
                                        st.write(gap["how_to_fill"])
                                if gap.get("suggested_bullet"):
                                    bullet_val = st.text_input(
                                        "Story text (edit if needed):",
                                        value=gap["suggested_bullet"],
                                        key=f"qf_bullet_{job['id']}_{gap_idx}",
                                    )
                                    if st.button("Add to stories & re-tailor", key=f"qf_add_{job['id']}_{gap_idx}"):
                                        new_story = Story(
                                            title=gap.get("skill", "Skill"),
                                            situation=f"Gap identified for {job.get('title', 'role')}",
                                            action=gap.get("how_to_fill", "Completed exercise"),
                                            result=bullet_val,
                                            tags=["gap-fill"],
                                            skills=[gap.get("skill", "")],
                                        )
                                        bank.add_story(new_story, dedup=True)
                                        from jobpilot.gaps import mark_gap_completed, _normalize_skill
                                        mark_gap_completed(_normalize_skill(gap.get("skill", "")), date.today().isoformat(), new_story.id)
                                        _delete_work(job["id"])
                                        for key in ["working_cv", "working_cl", "working_eval", "working_iterations"]:
                                            st.session_state.pop(key, None)
                                        st.success(f"Added story. Re-run tailoring to include it.")
                                        st.rerun()

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

                # Step 5: Generate & Download PDFs
                st.divider()
                output_dir = Path("output") / job["id"]
                cv_pdf = output_dir / "cv.pdf"
                cl_pdf = output_dir / "cover_letter.pdf"
                company_slug = job.get("company", "unknown").replace(" ", "_")
                title_slug = job.get("title", "role").replace(" ", "_")[:30]

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
                            output_dir.mkdir(parents=True, exist_ok=True)
                            with st.spinner("Rendering PDFs..."):
                                render_cv(cv_data, cv_pdf)
                                cl_render_data = {
                                    "name": cv_data.get("name", ""),
                                    "date": date.today().strftime("%B %d, %Y"),
                                    "company": job.get("company", ""),
                                    "job_title": job.get("title", ""),
                                    "body": cover_letter,
                                }
                                render_cover_letter(cl_render_data, cl_pdf)
                            st.rerun()
                        except Exception as exc:
                            st.error(f"PDF generation failed: {exc}")

                # Always show download buttons if PDFs exist on disk
                if cv_pdf.exists() and cl_pdf.exists():
                    st.success("PDFs ready")
                    col_dl1, col_dl2 = st.columns(2)
                    with col_dl1:
                        with open(cv_pdf, "rb") as f:
                            st.download_button("Download CV", f.read(), file_name=f"CV_{company_slug}_{title_slug}.pdf", mime="application/pdf")
                    with col_dl2:
                        with open(cl_pdf, "rb") as f:
                            st.download_button("Download Cover Letter", f.read(), file_name=f"CL_{company_slug}_{title_slug}.pdf", mime="application/pdf")

                    if job.get("url"):
                        st.markdown(f"[Apply now: {job.get('title', '')} at {job.get('company', '')}]({job['url']})")

                    # Mark as applied (with confirmation)
                    if "confirm_applied" not in st.session_state:
                        st.session_state["confirm_applied"] = False

                    if not st.session_state["confirm_applied"]:
                        if st.button("Mark as applied"):
                            st.session_state["confirm_applied"] = True
                            st.rerun()
                    else:
                        st.warning(f"Confirm: mark **{job.get('title', '')}** at **{job.get('company', '')}** as applied?")
                        col_yes, col_no = st.columns(2)
                        with col_yes:
                            if st.button("Yes, I applied", type="primary"):
                                apps_path = Path("data/applications.json")
                                apps = []
                                if apps_path.exists():
                                    try:
                                        apps = json.loads(apps_path.read_text(encoding="utf-8"))
                                    except Exception:
                                        apps = []
                                existing_ids = {a.get("job_id") for a in apps}
                                if job["id"] not in existing_ids:
                                    apps.append({
                                        "job_id": job["id"],
                                        "title": job.get("title", ""),
                                        "company": job.get("company", ""),
                                        "url": job.get("url", ""),
                                        "status": "submitted",
                                        "date_discovered": job.get("date_found", date.today().isoformat()),
                                        "date_submitted": date.today().isoformat(),
                                    })
                                else:
                                    for app in apps:
                                        if app.get("job_id") == job["id"]:
                                            app["status"] = "submitted"
                                            app["date_submitted"] = date.today().isoformat()
                                apps_path.parent.mkdir(parents=True, exist_ok=True)
                                apps_path.write_text(json.dumps(apps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                                st.session_state["confirm_applied"] = False
                                st.success(f"Marked as applied: {job.get('title', '')} at {job.get('company', '')}")
                                st.rerun()
                        with col_no:
                            if st.button("Cancel"):
                                st.session_state["confirm_applied"] = False
                                st.rerun()

                st.divider()
                if st.button("Back to job list", key="back_bottom"):
                    for key in ["working_job", "working_cv", "working_cl", "working_eval", "working_role_level", "working_iterations"]:
                        st.session_state.pop(key, None)
                    st.rerun()

        else:
            # Load applied job IDs for badges
            apps_path = Path("data/applications.json")
            applied_jobs = {}
            if apps_path.exists():
                try:
                    for app in json.loads(apps_path.read_text(encoding="utf-8")):
                        applied_jobs[app.get("job_id", "")] = app.get("status", "submitted")
                except Exception:
                    pass

            # Load dismissed job IDs
            dismissed_path = Path("data/dismissed_jobs.json")
            dismissed_ids = set()
            if dismissed_path.exists():
                try:
                    dismissed_ids = set(json.loads(dismissed_path.read_text(encoding="utf-8")))
                except Exception:
                    pass

            # Filter out dismissed jobs
            visible_jobs = [j for j in scored_jobs if j["id"] not in dismissed_ids]

            # Dashboard metrics
            new_today = sum(1 for j in visible_jobs if j.get("date_found") == date.today().isoformat())
            avg_score = sum(j.get("score", 0) for j in visible_jobs) / max(len(visible_jobs), 1)
            ready_count = sum(1 for j in visible_jobs if _load_work(j["id"]))

            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.markdown(f'<div class="metric-card"><div class="value">{len(visible_jobs)}</div><div class="label">Total Jobs</div></div>', unsafe_allow_html=True)
            with m2:
                st.markdown(f'<div class="metric-card"><div class="value">{new_today}</div><div class="label">New Today</div></div>', unsafe_allow_html=True)
            with m3:
                st.markdown(f'<div class="metric-card"><div class="value">{int(avg_score * 100)}%</div><div class="label">Avg Score</div></div>', unsafe_allow_html=True)
            with m4:
                st.markdown(f'<div class="metric-card"><div class="value">{ready_count}</div><div class="label">CVs Ready</div></div>', unsafe_allow_html=True)

            st.markdown("")
            col_dismiss_info, col_batch_toggle, col_summarize = st.columns([5, 3, 2])
            with col_dismiss_info:
                if len(visible_jobs) < len(scored_jobs):
                    st.caption(f"{len(scored_jobs) - len(visible_jobs)} dismissed jobs hidden")
            with col_batch_toggle:
                batch_mode = st.toggle("Batch mode", value=False)
            with col_summarize:
                needs_summary = [j for j in visible_jobs if not j.get("jd_summary") and (j.get("full_description") or j.get("description", "").strip())]
                if needs_summary:
                    if st.button(f"Summarize ({len(needs_summary)})"):
                        from jobpilot.llm import summarize_jd
                        progress_bar = st.progress(0)
                        for i, sj in enumerate(needs_summary):
                            jd_text = sj.get("full_description", "") or sj.get("description", "")
                            sj["jd_summary"] = summarize_jd(jd_text, sj.get("title", ""), sj.get("company", ""))
                            progress_bar.progress((i + 1) / len(needs_summary))
                        # Persist summaries
                        _pipeline = load_pipeline_jobs()
                        summary_map = {j["id"]: j.get("jd_summary", "") for j in needs_summary}
                        for pj in _pipeline:
                            if pj.get("id") in summary_map:
                                pj["jd_summary"] = summary_map[pj["id"]]
                        save_pipeline_jobs(_pipeline)
                        st.session_state["found_jobs"] = _pipeline
                        st.rerun()

            # Job cards
            for job in visible_jobs:
                saved = _load_work(job["id"])
                stage = saved.get("stage", "") if saved else ""

                # Build status badge HTML
                badge_html = ""
                if job["id"] in applied_jobs:
                    badge_html = _status_badge(f"Applied: {applied_jobs[job['id']]}", "applied")
                elif stage:
                    label = {"tailored": "CV ready", "revised": "Revised", "evaluated": "Evaluated", "auto_tailored": "CV ready"}.get(stage, stage)
                    badge_html = _status_badge(label, "ready")
                elif job.get("date_found") == date.today().isoformat():
                    badge_html = _status_badge("New", "new")

                score_html = _score_badge(job.get("score", 0))
                ref_count = job.get("referral_count", 0)
                referral_html = (
                    f'<span style="background:#7c3aed;color:white;padding:2px 8px;border-radius:12px;'
                    f'font-weight:600;font-size:0.8em;margin-left:4px;">🤝 {ref_count}</span>'
                    if ref_count else ""
                )
                date_found = job.get("date_found", "")
                location = job.get("location", "")
                source = job.get("source", "")
                posted = job.get("posted", "")
                meta_parts = [p for p in [location, source, posted, f"found: {date_found}" if date_found else ""] if p]

                # JD preview — show summary if available, else truncated description
                jd_summary = job.get("jd_summary", "")
                if jd_summary:
                    preview = jd_summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    desc_html = f'<div style="font-size:0.82em;color:#4b5563;margin-top:6px;line-height:1.4;">{preview}</div>'
                else:
                    desc_raw = job.get("full_description", "") or job.get("description", "")
                    if desc_raw.strip():
                        preview = desc_raw[:150].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "..."
                        desc_html = f'<div style="font-size:0.82em;color:#9ca3af;margin-top:6px;line-height:1.4;font-style:italic;">{preview}</div>'
                    else:
                        desc_html = ''

                st.markdown(f"""<div class="job-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <h4>{job['title']}</h4>
                        <div>{score_html}{referral_html} {badge_html}</div>
                    </div>
                    <div style="font-size:0.95em;color:#374151;">{job.get('company', '')}</div>
                    <div class="meta">{' &middot; '.join(meta_parts)}</div>
                    {desc_html}
                </div>""", unsafe_allow_html=True)

                if batch_mode:
                    col_check, col_action, col_link = st.columns([1, 2, 7])
                    with col_check:
                        st.checkbox("Select", key=f"batch_{job['id']}", label_visibility="collapsed")
                    with col_action:
                        pass
                    with col_link:
                        if job.get("url"):
                            st.caption(f"[Open listing]({job['url']})")
                else:
                    col_action, col_drop, col_link = st.columns([2, 1, 7])
                    with col_action:
                        if job["id"] in applied_jobs:
                            st.caption(f"_{applied_jobs[job['id']]}_")
                        else:
                            label = "Continue" if saved else "Work on this"
                            if st.button(label, key=f"select_{job['id']}"):
                                st.session_state["working_job"] = job
                                st.rerun()
                    with col_drop:
                        if st.button("Drop", key=f"dismiss_{job['id']}"):
                            dismissed_ids.add(job["id"])
                            dismissed_path.parent.mkdir(parents=True, exist_ok=True)
                            dismissed_path.write_text(json.dumps(sorted(dismissed_ids), indent=2) + "\n", encoding="utf-8")
                            st.rerun()
                    with col_link:
                        if job.get("url"):
                            st.caption(f"[Open listing]({job['url']})")

            # Batch dismiss button
            if batch_mode:
                selected_ids = {job["id"] for job in visible_jobs if st.session_state.get(f"batch_{job['id']}", False)}
                if selected_ids:
                    if st.button(f"Dismiss {len(selected_ids)} selected", type="primary"):
                        dismissed_ids.update(selected_ids)
                        dismissed_path.parent.mkdir(parents=True, exist_ok=True)
                        dismissed_path.write_text(json.dumps(sorted(dismissed_ids), indent=2) + "\n", encoding="utf-8")
                        st.rerun()


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

        # Status pipeline metrics
        status_order = ["discovered", "reviewed", "tailored", "submitted", "interview", "offer", "rejected"]
        status_colors = {
            "discovered": "#94a3b8", "reviewed": "#6366f1", "tailored": "#7c3aed",
            "submitted": "#2563eb", "interview": "#059669", "offer": "#16a34a", "rejected": "#dc2626",
        }
        cols = st.columns(len(status_order))
        for i, status in enumerate(status_order):
            count = statuses.count(status)
            color = status_colors.get(status, "#6b7280")
            with cols[i]:
                st.markdown(
                    f'<div class="metric-card"><div class="value" style="color:{color}">{count}</div>'
                    f'<div class="label">{status.title()}</div></div>',
                    unsafe_allow_html=True,
                )

        # Score analysis for submitted applications
        score_data = []
        for app in apps:
            work = _load_work(app.get("job_id", ""))
            if work and work.get("evaluation"):
                score_data.append({
                    "title": app.get("title", ""),
                    "company": app.get("company", ""),
                    "status": app.get("status", ""),
                    "score": work["evaluation"].get("overall_score", 0),
                    "shortlisted": work["evaluation"].get("would_shortlist", False),
                })
        if score_data:
            with st.expander("Score Analysis", expanded=False):
                for sd in score_data:
                    s_color = "#16a34a" if sd["score"] >= 7 else "#ca8a04" if sd["score"] >= 5 else "#dc2626"
                    sl = "Yes" if sd["shortlisted"] else "No"
                    st.markdown(
                        f'**{sd["title"]}** at {sd["company"]} — '
                        f'<span style="color:{s_color};font-weight:700;">{sd["score"]}/10</span> '
                        f'| Shortlist: {sl} | Status: {sd["status"]}',
                        unsafe_allow_html=True,
                    )

        st.markdown("")

        for app in apps:
            status = app.get("status", "discovered")
            color = status_colors.get(status, "#6b7280")
            submitted = app.get("date_submitted", "")
            submitted_label = f" | Submitted: {submitted}" if submitted else ""

            st.markdown(f"""<div class="job-card">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <h4>{app.get('title', '?')}</h4>
                    <span style="background:{color};color:white;padding:2px 12px;border-radius:12px;font-size:0.85em;">{status}</span>
                </div>
                <div style="font-size:0.95em;color:#374151;">{app.get('company', '?')}</div>
                <div class="meta">Discovered: {app.get('date_discovered', '?')}{submitted_label}</div>
            </div>""", unsafe_allow_html=True)

            col_status_sel, col_link = st.columns([3, 7])
            with col_status_sel:
                new_status = st.selectbox(
                    "Update status",
                    status_order,
                    index=status_order.index(status) if status in status_order else 0,
                    key=f"status_{app.get('job_id', '')}",
                    label_visibility="collapsed",
                )
                if new_status != status:
                    app["status"] = new_status
                    # Save winning cover letter on interview
                    if new_status == "interview":
                        work = _load_work(app.get("job_id", ""))
                        if work and work.get("cover_letter"):
                            winning_dir = Path("data/winning_cover_letters")
                            winning_dir.mkdir(parents=True, exist_ok=True)
                            (winning_dir / f"{app.get('job_id', 'unknown')}.json").write_text(
                                json.dumps({
                                    "job_title": app.get("title", ""),
                                    "company": app.get("company", ""),
                                    "cover_letter": work["cover_letter"],
                                    "role_level": work.get("role_level", ""),
                                    "score": work.get("evaluation", {}).get("overall_score", 0),
                                }, indent=2) + "\n", encoding="utf-8",
                            )
                    apps_path.write_text(
                        json.dumps(apps, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8",
                    )
                    st.rerun()
            with col_link:
                if app.get("url"):
                    st.caption(f"[View listing]({app['url']})")


# ============================================================
# TAB 5: Skills Gaps
# ============================================================
with tab_gaps:
    st.header("Skills Gap Tracker")
    st.caption("Aggregated gaps across all evaluated jobs, ranked by frequency.")

    from jobpilot.gaps import scan_all_gaps, load_gap_progress, mark_gap_completed, is_gap_completed

    gap_data = scan_all_gaps(_WORK_DIR)
    progress = load_gap_progress()

    quick_fill = gap_data["quick_fill"]
    hard_gaps = gap_data["hard_gaps"]
    completed_count = len(progress.get("completed", {}))

    # Metrics row
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f'<div class="metric-card"><div class="value">{len(quick_fill) + len(hard_gaps)}</div><div class="label">Total Gaps</div></div>', unsafe_allow_html=True)
    with m2:
        st.markdown(f'<div class="metric-card"><div class="value">{len(quick_fill)}</div><div class="label">Quick-fill</div></div>', unsafe_allow_html=True)
    with m3:
        st.markdown(f'<div class="metric-card"><div class="value">{len(hard_gaps)}</div><div class="label">Hard Gaps</div></div>', unsafe_allow_html=True)
    with m4:
        st.markdown(f'<div class="metric-card"><div class="value">{completed_count}</div><div class="label">Completed</div></div>', unsafe_allow_html=True)

    st.markdown("")
    hide_completed = st.toggle("Hide completed", value=True)

    # Quick-fill section
    if quick_fill:
        st.subheader("Quick-fill Skills")
        st.caption("These can be learned in a few hours. Most frequently required first.")

        for gap in quick_fill:
            if hide_completed and is_gap_completed(progress, gap["normalized"]):
                continue

            completed = is_gap_completed(progress, gap["normalized"])
            freq = gap["frequency"]
            total_jobs = len(list(_WORK_DIR.glob("*.json")))
            freq_pct = f"{freq}/{total_jobs} jobs"

            with st.container(border=True):
                col_title, col_freq, col_check = st.columns([6, 2, 2])
                with col_title:
                    label = f"~~{gap['skill']}~~" if completed else f"**{gap['skill']}**"
                    st.markdown(label)
                with col_freq:
                    color = "#16a34a" if freq >= 3 else "#ca8a04" if freq >= 2 else "#6b7280"
                    st.markdown(f'<span style="background:{color};color:white;padding:2px 10px;border-radius:12px;font-size:0.85em;">{freq_pct}</span>', unsafe_allow_html=True)
                with col_check:
                    if not completed:
                        if st.button("Mark done", key=f"gap_done_{gap['normalized']}"):
                            mark_gap_completed(gap["normalized"], date.today().isoformat())
                            st.rerun()
                    else:
                        st.caption("Done")

                # Jobs requiring this skill
                job_names = ", ".join(f"{j['title']} ({j['company']})" for j in gap["jobs"][:5])
                st.caption(f"Required by: {job_names}")

                if gap.get("how_to_fill"):
                    with st.expander("How to fill"):
                        st.write(gap["how_to_fill"])

                if gap.get("suggested_bullet") and not completed:
                    with st.expander("Add to Story Bank"):
                        bullet_text = st.text_area(
                            "Story text (edit if needed):",
                            value=gap["suggested_bullet"],
                            height=80,
                            key=f"gap_story_{gap['normalized']}",
                        )
                        if st.button("Add story", key=f"gap_add_{gap['normalized']}"):
                            bank_g = get_bank()
                            new_story = Story(
                                title=gap["skill"],
                                situation=f"Skill gap identified across {freq} job evaluations",
                                action=gap.get("how_to_fill", "Completed learning exercise"),
                                result=bullet_text,
                                tags=["gap-fill"],
                                skills=[gap["skill"]],
                            )
                            bank_g.add_story(new_story, dedup=True)
                            mark_gap_completed(gap["normalized"], date.today().isoformat(), new_story.id)
                            st.success(f"Added story and marked '{gap['skill']}' as done!")
                            st.rerun()

    # Hard gaps section
    if hard_gaps:
        st.subheader("Hard Gaps")
        st.caption("These require significant time/experience. Consider whether roles with these gaps are worth pursuing.")

        for gap in hard_gaps:
            freq = gap["frequency"]
            total_jobs = len(list(_WORK_DIR.glob("*.json")))
            freq_pct = f"{freq}/{total_jobs} jobs"

            with st.container(border=True):
                col_desc, col_freq = st.columns([8, 2])
                with col_desc:
                    st.markdown(gap["description"])
                with col_freq:
                    st.markdown(f'<span style="background:#dc2626;color:white;padding:2px 10px;border-radius:12px;font-size:0.85em;">{freq_pct}</span>', unsafe_allow_html=True)

                job_names = ", ".join(f"{j['title']} ({j['company']})" for j in gap["jobs"][:5])
                st.caption(f"Required by: {job_names}")

    if not quick_fill and not hard_gaps:
        st.info("No gaps found yet. Gaps are generated when you run the auto-tailor loop on a job.")
