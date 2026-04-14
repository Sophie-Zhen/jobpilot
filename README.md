# jobpilot

Personal AI job-search copilot (Route A): LangGraph orchestration for:

- collecting your stories and profile
- searching jobs daily
- ranking and human-reviewing opportunities
- tailoring resume draft per job
- drafting applications and tracking status
- generating skill-gap learning plans

## Why this scaffold

This project is optimized for one-person use:

- focus on practical workload reduction first
- keep a mandatory human checkpoint before application actions
- start with mock tools, then swap in real integrations incrementally

## Tech stack (initial)

- `LangGraph`: flow orchestration + interruption/human confirmation
- `Python`: single-process MVP runner
- `Supabase` (optional): structured persistence for stories/applications
- In-memory fallback store: run locally without external dependencies

## Project structure

- `src/jobpilot/graph.py`: main workflow graph
- `src/jobpilot/agents.py`: node logic (search, score, review, tailor, apply, plan)
- `src/jobpilot/job_sources.py`: open job API providers (`adzuna`, `arbeitnow`, `remotive`)
- `src/jobpilot/profile.py`: profile file initialization and loading
- `src/jobpilot/storage.py`: data layer (`InMemoryStore`, `SupabaseStore`)
- `src/jobpilot/config.py`: env-driven settings
- `src/jobpilot/cli.py`: local runner with interactive review step
- `.env.example`: environment template

## Quick start

1) Install dependencies:

```bash
pip install -e .
```

2) Create env file:

```bash
cp .env.example .env
```

3) Run:

```bash
jobpilot
```

You will see a review checkpoint and choose approved job IDs before the flow continues.

4) Initialize and edit your real profile:

```bash
jobpilot init-profile
```

This creates `data/profile.json` if it does not exist.

## Current flow

Main application line:

`load_profile -> load_stories -> search_jobs -> score_jobs -> review_jobs (interrupt) -> tailor_resume -> apply_jobs`

Learning line (independent from application progress):

`load_profile -> learning_progress -> learning_plan`

Shared influence:

- `score_jobs` uses both `profile` and `learning_progress` (in-progress skills add weighted bonus).

## What is mocked today

- learning progress data (currently static sample progress)
- resume tailoring prompt/model calls (currently deterministic text summary)
- application submission (currently creates local/db draft records)

## Job search providers (all via single RAPIDAPI_KEY)

- `jsearch` (default) — Google Jobs aggregator, 200 req/month free
- `linkedin` — LinkedIn jobs via Fantastic Jobs, 25 req/month free (100 jobs/call)
- `active_jobs_db` — 175k+ career sites/ATS, 25 req/month free (100 jobs/call)
- `arbeitnow` — European fallback, no key needed
- `remotive` — remote jobs fallback, no key needed

Fallback chain: `jsearch -> linkedin -> active_jobs_db -> arbeitnow`

Job search query comes from your `profile`:

- first `target_roles` item
- first `preferred_keywords` item
- automatic query fallback: `role + keyword -> role -> secondary role`

Profile filters are applied after fetching:

- `locations` (currently set to `Dublin`)
- `excluded_keywords`

## Next steps for your real setup

1) Replace `search_jobs_node` with real job source tools (API first, browser fallback).
2) Replace `tailor_resume_node` with LLM prompt + resume template renderer.
3) Replace `apply_jobs_node` with browser automation adapter and safety checks.
4) Add a lightweight UI (or Notion sync) for daily review and status dashboard.
5) Add scheduled runner (cron/GitHub Actions) for automatic daily search.