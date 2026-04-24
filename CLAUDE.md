# JobPilot — Project Context

## What this is

Personal AI job-search copilot for Sophie Zhen. Searches for jobs, maintains a career story bank, tailors CVs and cover letters per job with role-level awareness, evaluates them independently, generates ATS-friendly PDFs, and tracks applications.

## User context

Sophie is an AI/NLP engineer job hunting in Dublin, Ireland (Stamp 1G visa). 13 years experience in tax administration (Shanghai), career change to AI via MSc Computer Science (NLP) at DCU (First-Class Honours, top 5%). She wants tools that compound over time, prefers the ambitious approach, and has zero attachment to sunk cost.

## Architecture

```
data/master_cv.json  ← single source of truth for all career content
data/profile.json    ← job search config only (target_roles, locations, excluded_keywords)
data/stories.json    ← supplementary stories (extra context beyond master CV)
data/pipeline_jobs.json ← persisted search results
data/work/{job_id}.json ← per-job working state (CV, cover letter, evaluation)
data/applications.json  ← application tracking with status history
```

## Tech stack

- **Python 3.11** in conda env `jobpilot`
- **LangGraph** for workflow orchestration (graph.py)
- **Claude Code CLI** (`claude -p`) for ALL LLM calls — no OpenAI dependency
- **LaTeX** (pdflatex) for PDF generation via Jinja2 templates
- **Streamlit** for web UI (app.py)
- **Pydantic** for data models

## Key design decisions

1. **Claude Code as LLM backend** — all calls go through `claude -p --output-format json` via stdin. No API keys needed beyond Claude Code subscription.
2. **Master CV approach** — `data/master_cv.json` has all verified career data. Tailoring only generates micro-adjustments (summary, bullet selection, skill ordering), never rewrites from scratch.
3. **Role-level awareness** — `classify_role_level()` detects graduate/junior/mid/senior. Each level has specific rules (graduate: don't lead with "13 years experience"; senior: emphasize leadership and scale).
4. **Single source of truth** — `master_cv.json` owns contact, education, experience, projects, skills, awards. `profile.json` only has search preferences.
5. **Story dedup** — Claude checks new stories against existing ones before adding. Keyword overlap fallback (>70% threshold).
6. **Web search for jobs** — Claude Code with WebSearch/WebFetch tools searches LinkedIn directly. Falls back to RapidAPI sources.
7. **Persistent workflow** — search results, per-job work state (CV, cover letter, eval), and application tracking all persist to disk. Can resume from any stage.

## Pipeline flow (15 LangGraph nodes)

```
START → load_profile → load_stories → search_jobs → score_jobs → review_jobs
                     → learning_progress → score_jobs / learning_plan → END

review_jobs → fetch_jds → tailor_resume → review_tailored → evaluate_cv → review_evaluation → render_pdfs → apply_jobs → END
```

Scheduled mode (cron): skips all review interrupts, saves scored jobs for later review.

## File overview

| File | Purpose |
|------|---------|
| `src/jobpilot/llm.py` (590 lines) | All LLM functions: `tailor_cv`, `generate_cover_letter`, `classify_role_level`, `evaluate_cv`, `fetch_full_jd`, `search_jobs_web`, `structure_story`, `refine_story`, `revise_cv`, `import_stories` |
| `src/jobpilot/app.py` (618 lines) | Streamlit UI: 4 tabs (Add Content, Story Bank, Job Pipeline, Applications). Pipeline tab has one-at-a-time job workflow with persistent state. |
| `src/jobpilot/cli.py` (514 lines) | CLI with argparse subparsers: `run`, `init-profile`, `story add/list/import/migrate/edit/delete/refine`, `status`, `update`, `feedback` |
| `src/jobpilot/agents.py` (465 lines) | LangGraph nodes: load_profile, load_stories, search_jobs, score_jobs, review_jobs, fetch_jds, tailor_resume, review_tailored, evaluate_cv, review_evaluation, render_pdfs, apply_jobs, learning_progress, learning_plan |
| `src/jobpilot/job_sources.py` (441 lines) | Job search providers (jsearch, linkedin, active_jobs_db, arbeitnow, remotive), dedup filter (seen_jobs.json + applications.json), budget tracking |
| `src/jobpilot/stories.py` (233 lines) | Story Pydantic model, StoryBank class (CRUD, Claude-powered relevance ranking, keyword fallback, dedup) |
| `src/jobpilot/renderer.py` (123 lines) | LaTeX escaping (placeholder-based to avoid double-escape), Jinja2 custom delimiters, pdflatex compilation |
| `src/jobpilot/graph.py` (67 lines) | LangGraph StateGraph wiring — 15 nodes |
| `src/jobpilot/profile.py` (74 lines) | Loads unified profile: contact from master_cv.json, search config from profile.json |
| `src/jobpilot/config.py` (41 lines) | Settings dataclass loaded from .env |
| `src/jobpilot/state.py` (21 lines) | JobPilotState TypedDict |
| `src/jobpilot/storage.py` (102 lines) | StoryStore protocol, InMemoryStore, SupabaseStore (deferred) |
| `templates/cv.tex` | ATS-friendly LaTeX CV template with Jinja2 placeholders. Experience before Education. Clickable email/LinkedIn/GitHub links. |
| `templates/cover_letter.tex` | LaTeX cover letter template |

## Testing

```bash
conda run -n jobpilot python -m pytest tests/ -v
```

39 tests covering: escape_latex (14 tests), StoryBank CRUD + keyword fallback (16 tests), dedup filter + pruning (5 tests), PDF rendering (4 tests).

## Running

```bash
conda activate jobpilot
streamlit run src/jobpilot/app.py    # Web UI at localhost:8501
jobpilot run                          # CLI pipeline
jobpilot story list                   # View stories
jobpilot status                       # View applications
```

## Setup on new machine

```bash
conda create -n jobpilot python=3.11 -y
conda activate jobpilot
pip install -e .
pip install pytest streamlit pypdf
```

Requires: pdflatex (`brew install --cask mactex-no-gui` on macOS), Claude Code CLI.

## Current state

- 17 stories in story bank (imported from CV)
- 9 projects in master CV
- 15 jobs found from latest search (persisted in pipeline_jobs.json)
- 1 job has work-in-progress (saved in data/work/)
- Role-level CV customization working (graduate/junior/mid/senior)
- Third-party evaluation working (recruiter scorer)
- Feedback/revision loop in UI
- Application tracking with status history

## TODOs

See `TODOS.md` for future features: messaging integration (WhatsApp/Telegram), auto-apply, scheduled search, gap analysis, rejection pattern analysis, drop LaTeX for weasyprint.

## Important patterns

- All LLM calls: `llm.py:_call_claude(prompt, timeout, tools)` — passes prompt via stdin, parses JSON envelope response
- Master CV tailoring: `llm.py:tailor_cv()` loads master_cv.json, asks Claude for adjustments only (summary, bullet indices, project IDs, skill order), applies them to the master data
- Story search: `stories.py:StoryBank.find_similar()` — asks Claude to rank stories by relevance, keyword fallback
- LaTeX escaping: `renderer.py:escape_latex()` — placeholder-based to avoid double-escaping backslash + braces
- Persistent state: search results in `pipeline_jobs.json`, per-job work in `data/work/{id}.json`

## Known issues

- Web search timeout: 300s limit, occasionally times out for complex multi-role queries
- Cover letter sometimes includes preamble text ("Here is the cover letter:") — prompt instructs against it but Claude occasionally ignores
- `revise_cv` function referenced in app.py UI but may need verification on new machine
