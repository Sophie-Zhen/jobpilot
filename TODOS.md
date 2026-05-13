# JobPilot — TODOs

_Last updated: 2026-05-13_

## Current Focus
Discovery layer live: `jobpilot discover` polls 9 Tier-1 ATS endpoints + 4 LinkedIn keyword searches via opencli, all Ireland-filtered. ~200 jobs in pipeline + daily refresh. Next is master_cv data-gap audit (using the gaps tool against the new pipeline) to lift ATS scores, then build the review/notify side: Telegram daily digest + Streamlit-lite Review Queue.

## Open Questions / Blockers
- ATS threshold 0.75 still a guess — interim 0.60 while master_cv is being fixed; real calibration once ≥15 submissions have outcomes
- Decide whether to migrate from `claude -p` CLI to Anthropic SDK before doing prompt caching (REDESIGN week-1 #5)
- Split hunting time: ~60% deep-outreach flywheel (Compliance Orchestrator + warm intros), ~40% jobpilot volume — currently skewed to volume
- LangGraph teardown deferred — watch whether the 15-node orchestrator becomes maintenance drag before its payoff

## Todo

### Master_cv data-gap audit (highest leverage now)
- [ ] Run `jobpilot gaps --recompute` against current `data/work/*.json`, then audit `data/master_cv.json` + stories against the output. For each must-have keyword in ≥30% of jobs AND truthfully supported by actual experience (MCP/RAG/CI-CD/testing/FastAPI/LangGraph), add to master skills and rewrite bullets to surface the keyword. Goal: every existing work file's ATS score moves up by ≥0.10 after re-tailor.
- [ ] Add `Java`, `Spring Boot`, `Kotlin`, `Scala`, `Go`, `.NET` to `profile.json` `excluded_keywords` — gaps output + market research confirmed these are noise sources (target_roles narrowing already done in `7e0401e`)
- [ ] Drop `ats_threshold` from 0.75 → 0.60 as interim during master_cv work (revert once audit done)

### Pillar 3 — Network outreach (the missing channel)
- [ ] Implement outreach module: for each P0 job, generate LinkedIn search URL (hiring manager + recruiter at $company) + DCU alumni search URL + 3-line DM template (JD hook + career-change bridge + referral ask). Push to Telegram for manual copy-send. Hard red line: never auto-send DMs.

### Review surface (replacement for current Streamlit pipeline tab)
- [ ] Telegram daily digest bot: pulls discover output, sends top-N ranked jobs with inline buttons (Apply / Skip / Save / Outreach). Pillar 2 + 3 share this surface. `notify.py:send_telegram` already wired in one place; need scheduled cron + ranked digest payload.
- [ ] Streamlit-lite: trim to 3 tabs (Master CV editor, Story Bank editor, Review Queue with P0/P1/P2 columns). Delete the auto-tailor / evaluate UI — those become CLI-only.

### Discovery layer follow-ups
- [ ] Tighten `is_dublin_eligible()` in `discovery/ats_sources.py` — currently "ireland" alone accepts Waterford/Cork/Galway on-site postings. Should only accept "dublin" or ("ireland" + "remote"). Deferred 2026-05-13 — Sophie's current priority is interview volume, geography secondary.
- [ ] Merge `~/code/dublin_ai_jobs_bot` into `src/jobpilot/discovery/` — port the IrishJobs.ie scraper for non-LinkedIn coverage. Currently a separate cron pipeline.
- [ ] Niche-gold P0 scoring: jobs at `target_companies.json` companies with `niche_gold: true` auto-promoted to P0 regardless of keyword overlap

### Validate the ATS loop end-to-end
- [ ] Run auto-tailor loop live on one real job and verify iteration 2+ targets ATS gaps; screenshot the ATS card for confirmation
- [ ] Calibrate `ats_threshold` once ≥15 submissions have outcomes; correlate `overall` with response rate

### Architectural cleanup (lower priority)
- [ ] Rip out LangGraph — replace 15-node graph with linear `pipeline(job)` function (~100 lines deleted, no behaviour loss). Deferred until it blocks progress.
- [ ] Prompt caching — migrate `tailor_cv`, `evaluate_cv`, `classify_role_level` to Anthropic SDK with `cache_control: ephemeral` on the master_cv prefix (REDESIGN week-1 #5)
- [ ] Drop LaTeX — replace `pdflatex` with weasyprint (HTML/CSS → PDF) so the project doesn't need a system-wide TeX install

### Flywheel (the channel jobpilot can't optimize)
- [ ] Pick 3-5 genuinely-fit companies (compliance-adjacent, AI-native, Stamp 1G-friendly) for deep outreach: warm intros via LinkedIn/DCU network, research their actual AI problems, send custom messages. Not volume.
- [ ] Ship Agentic Compliance Orchestrator MVP — LLM reads a regulation excerpt, extracts rules, checks a small dataset for violations, outputs a compliance report. The deployed/documented project IS the CV for the tax+AI niche (Walkers / Maples / Big 4 / RegTech).

## Future Features

- [ ] **Auto-apply via browser automation** — Playwright on Greenhouse/Lever (NOT LinkedIn — ban risk). Humans approve each submission. Hard red line: never automate LinkedIn Easy Apply or DMs.
- [ ] **Rejection pattern analysis** — Use feedback data to identify patterns (missing keywords, role-level mismatch) and improve future tailoring automatically.
- [ ] **Project blueprints from gaps** — `jobpilot gaps` aggregates rare missing skills across the pipeline and generates concrete weekend project specs to fill them.
