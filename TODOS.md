# JobPilot — TODOs

_Last updated: 2026-04-24_

## Current Focus
ATS simulator landed (`a329bbd`). Shifting from "build the gate" to "close the data gap" — `master_cv.json` is under-representing actual capability (MCP, RAG, CI/CD, testing truthfully in stories but not in master), which drags every ATS score. Also narrowing target roles upstream to stop surfacing Java backend jobs with "AI" in the title.

## Open Questions / Blockers
- ATS threshold 0.75 is a guess — interim lower to 0.60 while master_cv gaps are being fixed, real calibration once ≥15 submissions have outcomes
- Decide whether to migrate from `claude -p` CLI to Anthropic SDK before doing prompt caching (REDESIGN week-1 #5)
- Split hunting time: ~60% deep-outreach flywheel (3-5 fit companies, the Compliance Orchestrator project), ~40% jobpilot volume channel — currently skewed too far toward volume

## Todo

### Close the data gap (highest leverage, this week)
- [ ] Quick `jobpilot gaps` CLI: aggregate `missing_must` across `data/work/*.json`, print top-N by frequency. 60-min first version — do this BEFORE the master_cv audit so the audit is data-driven, not intuition-driven
- [ ] Audit `data/master_cv.json` + stories against the aggregated missing-keyword list: for each keyword in ≥30% of jobs AND truthfully supported by actual experience (MCP/RAG/CI-CD/testing/FastAPI/LangGraph), add to master skills and rewrite bullets to surface the keyword. Goal: every existing work file's ATS score moves up by ≥0.10 after re-tailor
- [ ] Tighten `data/profile.json`: add `Java`, `Spring Boot`, `Kotlin`, `Scala`, `Go` (language), `.NET` to `excluded_keywords`; split `target_roles` into narrower variants (`LLM Application Engineer`, `Agent Engineer`, `NLP Engineer`, `AI Solutions Engineer`) so discovery stops surfacing unwinnable roles
- [ ] Drop `ats_threshold` from 0.75 → 0.60 as an interim while master_cv is being fixed (revert once master audit is done)

### Validate the ATS loop end-to-end
- [ ] Run auto-tailor loop live on one real job and verify iteration 2+ targets ATS gaps; screenshot the ATS card for confirmation
- [ ] Calibrate ATS threshold once ≥15 submissions have outcomes; correlate `overall` with response rate (REDESIGN week-2 #8)

### Consolidation + automation (REDESIGN week 1-2)
- [ ] Merge `~/code/dublin_ai_jobs_bot` → `src/jobpilot/discovery/`; kill the `jobs.db → pipeline_jobs.json` handoff, port GH Actions cron (REDESIGN week-1 #3)
- [ ] Wire `src/jobpilot/notify.py` + build daily digest email with top-N ranked by ATS score; convert Streamlit to review queue (REDESIGN week-1 #4)
- [ ] Prompt caching — migrate `tailor_cv`, `evaluate_cv`, `classify_role_level` to Anthropic SDK with `cache_control: ephemeral` on the master_cv prefix (REDESIGN week-1 #5)
- [ ] Wire `src/jobpilot/gaps.py` into daily digest (surface top-5 gaps in the email) (REDESIGN week-2 #6)
- [ ] "Open + Copy" bundle in Streamlit review queue — opens JD URL, copies CV PDF path + cover letter text to clipboard, logs timestamp (REDESIGN week-2 #7)

### Flywheel (the channel jobpilot can't optimize)
- [ ] Pick 3-5 genuinely-fit companies (compliance-adjacent, AI-native, Stamp 1G-friendly) for deep outreach: warm intros via LinkedIn/DCU network, research their actual AI problems, send custom messages. Not volume.
- [ ] Start Agentic Compliance Orchestrator MVP — define scope (LLM reads a regulation excerpt, extracts rules, checks a small dataset for violations, outputs a compliance report), ship a week-1 prototype. This is the rare 13yr tax + AI combination nobody else has; the deployed, documented project IS the CV for career-change hires at Walkers / Maples / Big 4 / compliance-aware AI firms

## Future Features

- [ ] **Messaging integration** — Interact with JobPilot through WhatsApp, Telegram, or other messaging platforms. Add stories, get job alerts, review CVs, and approve applications directly from chat instead of the Streamlit UI or CLI.

- [ ] **Auto-apply via browser automation** — Use Playwright/Selenium to submit applications through real job portals (LinkedIn Easy Apply, Greenhouse, Lever, Workday). Requires careful safety checks before submission.

- [ ] **Scheduled daily search** — Cron job runs `jobpilot run --scheduled` every morning, saves new jobs for later review. Add desktop/email notifications when high-scoring jobs are found.

- [ ] **Gap analysis + project blueprints** — `jobpilot gaps` analyzes story bank vs target job requirements, generates concrete weekend project specs to fill skill gaps.

- [ ] **Rejection pattern analysis** — Use feedback data to identify patterns (missing keywords, role-level mismatch) and improve future tailoring automatically.

- [ ] **Drop LaTeX dependency** — Replace pdflatex with weasyprint (HTML/CSS to PDF) for easier distribution. No system dependency needed.
