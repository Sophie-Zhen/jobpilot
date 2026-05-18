# JobPilot — TODOs

_Last updated: 2026-05-17_

## Current Focus
End-to-end shipped: 3-variant CV tailor (grad/tech_eng/regtech) + FRAMING_RULES identity layer + per-job `jobpilot tailor` subcommand + page-count check. First 2 real submissions today: Tines (tech_eng) + Bending Spoons (grad). PII separated from tracking, git history scrubbed via filter-repo, GitHub repo recreated and ready to flip public. Next: README polish for portfolio audience, monitor 2-week callback window, then Pillar 3 outreach module.

## Open Questions / Blockers
- ATS threshold 0.75 still a guess — was lowered to 0.60 as interim; unclear if reverted. Verify and calibrate once ≥15 submissions have outcomes (currently 2)
- Decide whether to migrate from `claude -p` CLI to Anthropic SDK before doing prompt caching (REDESIGN week-1 #5)
- Split hunting time: deep-outreach flywheel (Compliance Orchestrator + warm intros) vs jobpilot volume. Currently 100% volume (2 apps shipped today, 0 outreach). Recalibrate after 2-week callback window.
- LangGraph teardown deferred — watch whether the 15-node orchestrator becomes maintenance drag before its payoff

## Todo

### Open-source polish (portfolio readiness)
- [ ] Polish README for portfolio audience — current README still says "currently mocked" / "deterministic text summary" / RapidAPI fallback chain, which doesn't reflect the LLM-powered v2 (Claude Code, 3-variant tailor, FRAMING_RULES, end-to-end `jobpilot tailor`). Recruiter-readable rewrite.
- [ ] Fix regtech variant × junior/grad role_level conflict: `FRAMING_RULES_REGTECH` says "Tax bureau bullets: include all 4" but `ROLE_LEVEL_INSTRUCTIONS["junior"]` says "pick 2 bullets" — LLM follows the role_level rule and produces a too-thin regtech CV. Manual fix today on Induct ran fine but this needs a prompt-design fix (e.g. variant-rules override role-level for tax_bureau bullet count, or merge into a single coherent rule per (variant, role_level) pair).
- [ ] **Tighten summary-attribution rule in FRAMING_RULES**: 2026-05-18 audit caught Apple CV claiming "fine-tuning GliNER for financial NER **during a Huawei research internship**" — GliNER and Huawei are independent. LLM has freedom in the summary field and can cross-attribute. Add an ATTRIBUTION SAFETY rule to all 3 variants: only attribute projects/experiences as listed in the available blocks, do not combine separate items, prefer open phrasing ("built X") over false attribution ("built X at Y"). Tines (yesterday) also softly mischaracterized Huawei work as "LLM-based information extraction" — same root cause. Until fixed, summary review is mandatory before every submission.
- [ ] **Add date-awareness to tailoring prompt**: Today's Apple summary first re-fix said "currently a research intern at Huawei" — but the internship ended Dec 2025. The tailor LLM has no calendar context. Inject `current_date` + each experience's `dates` field explicitly into the prompt so it can correctly choose past vs present tense.
- [ ] **Strip duplicate sign-off in cover letters at generation time**: `generate_cover_letter` consistently appends "Best regards, Songhui Zhen" / "Sincerely, Songhui Zhen" to the body — but `templates/cover_letter.tex` ALREADY adds "Sincerely, <name>" after the body. Result: every cover letter has a double sign-off. Confirmed on all 4 today (Apple/MongoDB/Twilio/Induct) and almost certainly on yesterday's submissions (Tines/Bending Spoons) too. Fix at prompt level: instruct LLM to end with the closing thank-you and stop — do not add salutation/signature. Also extend ATTRIBUTION SAFETY rule to cover letters (Apple's body said "At Walkers Global I built..." implying employment; Walkers was a DCU MSc practicum partner, not employer).
- [ ] Rename `is_dublin_eligible()` → `is_ireland_eligible()` in `discovery/ats_sources.py` — function name now lies (filter is Ireland-wide per `memory/feedback_location_scope.md`).
- [ ] (low) Add `jobpilot apply <job_id> --variant X --url Y` CLI subcommand for manual application logging — currently writing directly to `applications.json`.

### Master_cv data-gap audit (residual)
- [ ] Verify whether `ats_threshold` is currently 0.75 (default) or 0.60 (interim) — revert to 0.75 once confirmed audit/enrichment is sufficient (L1+L2+L3 + my_assistant + old-CV recovery shipped this week).

### Pillar 3 — Network outreach (the missing channel)
- [ ] Implement outreach module: for each P0 job, generate LinkedIn search URL (hiring manager + recruiter at $company) + DCU alumni search URL + 3-line DM template (JD hook + career-change bridge + referral ask). Push to Telegram for manual copy-send. Hard red line: never auto-send DMs.

### Review surface (replacement for current Streamlit pipeline tab)
- [ ] Telegram daily digest bot: pulls discover output, sends top-N ranked jobs with inline buttons (Apply / Skip / Save / Outreach). Pillar 2 + 3 share this surface. `notify.py:send_telegram` already wired in one place; need scheduled cron + ranked digest payload.
- [ ] Streamlit-lite: trim to 3 tabs (Master CV editor, Story Bank editor, Review Queue with P0/P1/P2 columns). Delete the auto-tailor / evaluate UI — those become CLI-only.

### Discovery layer follow-ups
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
- [ ] **Personal wiki / brag-doc thinking layer** — Obsidian vault at `wiki/` inside jobpilot repo, markdown + YAML frontmatter, bridges to `stories.json` via a converter script. Two-layer model: wiki for thinking, JSON for shipping. Triggers: first real interview OR CV-pass idle period. Anchors: Julia Evans brag-doc template, Karpathy LLM-wiki pattern, Andy Matuschak evergreen notes. See `memory/reference_personal_wiki.md`.
