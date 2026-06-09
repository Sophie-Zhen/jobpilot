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

## Pipeline flow (14 LangGraph nodes)

```
START → load_profile → load_stories → search_jobs → score_jobs → review_jobs
                     → learning_progress → score_jobs / learning_plan → END

review_jobs → fetch_jds → tailor_resume → review_tailored → evaluate_cv → review_evaluation → render_pdfs → apply_jobs → END
```

Scheduled mode (cron): skips all review interrupts, saves scored jobs for later review.

## File overview

| File | Purpose |
|------|---------|
| `src/jobpilot/llm.py` | All LLM functions: `tailor_cv`, `generate_cover_letter`, `classify_role_level`, `evaluate_cv`, `fetch_full_jd`, `search_jobs_web`, `structure_story`, `refine_story`, `revise_cv`, `import_stories` |
| `src/jobpilot/app.py` | Streamlit UI: 4 tabs (Add Content, Story Bank, Job Pipeline, Applications). Pipeline tab has one-at-a-time job workflow with persistent state. |
| `src/jobpilot/app_runner.py` | Entry point for `jobpilot-ui` script that launches Streamlit |
| `src/jobpilot/cli.py` | CLI with argparse subparsers: `run`, `tailor`, `init-profile`, `story add/list/import/migrate/edit/delete/refine`, `status`, `update`, `feedback`, `gaps` |
| `src/jobpilot/agents.py` | LangGraph nodes: load_profile, load_stories, search_jobs, score_jobs, review_jobs, fetch_jds, tailor_resume, review_tailored, evaluate_cv, review_evaluation, render_pdfs, apply_jobs, learning_progress, learning_plan |
| `src/jobpilot/ats.py` | Resume relevance + parseability check driving the auto-tailor loop. NOT a "beat the bot" tool — tech ATSes don't auto-reject; this is a human-7-second-scan relevance proxy. Keyword coverage + `keyword_stuffing_penalty` (recruiters penalize stuffing) + PDF parseability + format audit |
| `src/jobpilot/gaps.py` | Skill gap analysis: aggregate ATS keyword gaps across JDs |
| `src/jobpilot/referrals.py` | Referral discovery (book ch.2, the 10x lever). Loads the candidate's LinkedIn `Connections.csv` export, token-subset company match → who can refer you. **Referral = a scoring signal**: both scorers (`job_sources.score_jobs` via optional `connections=` param, and the `score_jobs_node` graph node) add a `_REFERRAL_BOOST` (0.15) and a `referral_count` field to jobs at a connected company, so referable roles float to the top of the pipeline (🤝 badge in the UI). Surfaced in the pipeline tab (per working job) + CLI: `jobpilot referrals [company]`, `jobpilot referrals` (top companies), `jobpilot referrals --targets` (cross-reference vs target_companies.json → which targets have a warm path). CSV path = `Settings.connections_csv` (env `CONNECTIONS_CSV`, default `data/connections.csv`, gitignored PII). No scraping — manual export only. |
| `src/jobpilot/job_sources.py` | Job search providers (jsearch, linkedin, active_jobs_db, arbeitnow, remotive), dedup filter (seen_jobs.json + applications.json), budget tracking |
| `src/jobpilot/discovery/ats_sources.py` | Tier-1 discovery: poll target-company ATS endpoints for Dublin jobs |
| `src/jobpilot/discovery/opencli_source.py` | Tier-2 discovery: LinkedIn/Indeed scraping via opencli |
| `src/jobpilot/api.py` | FastAPI backend exposing pipeline data |
| `src/jobpilot/notify.py` | Telegram notification helpers |
| `src/jobpilot/inbox_sync.py` | Phase 4 + 4.5 + 4.6: multi-account Gmail → applications.json. OAuth per account, LLM classifier (rejection/interview/info_request/ack/other), fuzzy company match, Telegram push on status change. Phase 4.5 auto-bootstraps new rows from unmatched ack emails. Phase 4.6 adds `--non-interactive` mode: when an OAuth grant expires under launchd, push a Telegram re-auth alert and exit non-zero instead of opening a browser. Read-only on email side; never replies. |
| `infra/launchd/inbox-sync.plist.example` | macOS launchd UserAgent template — runs `jobpilot inbox-sync --non-interactive` at 21:00 (9 PM) daily, with launchd's built-in coalesced catch-up when the Mac was asleep at fire time. Copy to `~/Library/LaunchAgents/` then `launchctl load`. Setup notes in the file header. |
| `infra/launchd/bot.plist.example` | macOS launchd UserAgent template for the Telegram polling daemon. RunAtLoad+KeepAlive so it auto-starts on login and auto-restarts on crash (ThrottleInterval 30s). Hosts /start, /ping, /sync, plus all digest-card button taps. Copy to `~/Library/LaunchAgents/` then `launchctl load`. |
| `src/jobpilot/stories.py` | Story Pydantic model, StoryBank class (CRUD, Claude-powered relevance ranking, keyword fallback, dedup) |
| `src/jobpilot/renderer.py` | LaTeX escaping (placeholder-based to avoid double-escape), Jinja2 custom delimiters, pdflatex compilation |
| `src/jobpilot/graph.py` | LangGraph StateGraph wiring — 14 nodes |
| `src/jobpilot/profile.py` | Loads unified profile: contact from master_cv.json, search config from profile.json |
| `src/jobpilot/config.py` | Settings dataclass loaded from .env |
| `src/jobpilot/state.py` | JobPilotState TypedDict |
| `src/jobpilot/storage.py` | StoryStore protocol, InMemoryStore, SupabaseStore (deferred) |
| `templates/cv.tex` | ATS-friendly LaTeX CV template with Jinja2 placeholders. Sections emitted via a `section_order` dispatch loop (per-variant order from `llm.SECTION_ORDER_BY_VARIANT`; `renderer.DEFAULT_SECTION_ORDER` fallback) — career-changer/grad puts Projects+Skills above Experience. Optional per-role `Technologies:` line (validated against master skills, never fabricated). Clickable email/LinkedIn/GitHub links. |
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
