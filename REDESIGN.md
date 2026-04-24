# JobPilot Redesign Plan

> Drafted 2026-04-23 after a senior-engineer review of jobpilot + jobradar
> (`/Users/sophie/code/dublin_ai_jobs_bot`). This file is working context — not
> doctrine. Update or delete it once the consolidation is done.

## Why this exists

Sophie's stated pain point is **CV filter pass-rate** (ATS rejection before
human eyes), and her stated goal is **automating the repeated labor** of job
hunt. Today neither project actually solves either:

- **jobpilot** generates tailored PDFs but `evaluate_cv` (`src/jobpilot/llm.py:627`)
  is Claude role-playing a recruiter — `ats_issues` is a vibe, not a parseability
  test. There is no objective signal to optimize against.
- **jobpilot** also requires human babysitting at 3 LangGraph `interrupt()` points
  (`src/jobpilot/agents.py:93, 212, 285`). The `auto_tailor_loop` that *could*
  iterate unattended (`src/jobpilot/llm.py:753`) is wired into Streamlit only,
  not into the pipeline.
- **jobradar** is the only piece that actually runs unattended (GH Actions, every
  30 min) but does nothing for CVs. It feeds jobs to jobpilot via a JSON file
  handoff (`jobs.db → ~/code/jobpilot/data/pipeline_jobs.json`) — a distributed
  system made of files, which is a smell.
- Two repos exist because each felt incomplete; consolidation has been deferred.

## Architectural direction

One repo, two layers. `jobpilot/discovery/` (formerly jobradar) finds jobs.
`jobpilot/pipeline/` tailors, scores against a real ATS simulator, and queues
ready-to-send applications for daily human review. The loop closes on an
**objective ATS score**, not an LLM opinion.

```
discovery (cron, every 30m)
    ↓ writes to a single SQLite (no JSON handoff)
score + dedup
    ↓
tailor_cv  ──►  ATS simulator  ──►  score ≥ threshold?
    ▲                                  │
    └────── auto_tailor_loop ◄─────────┘ (max 3 iterations)
                                        │
                                       yes
                                        ↓
                              ready-to-send queue
                                        ↓
                          daily digest email (top N)
                                        ↓
                              human approves in Streamlit
                                        ↓
                              "open + copy bundle" (manual paste)
```

Removing the 3 `interrupt()` calls from the happy path is what makes
"cron-ready" actually true. Streamlit becomes a review/approve queue, not a
step-through wizard.

---

## TODO — week 1 (build order matters)

### 1. ATS simulator (highest leverage — do this first)

The single change that converts "vibes" into a number you can optimize.

- [ ] Create `src/jobpilot/ats.py` as a standalone module (no LangGraph deps).
- [ ] `parse_pdf(path) -> dict` — re-extract a rendered CV PDF using
      `pdfplumber` and `pypdf` (the libraries Workday / Greenhouse / Lever
      parsers use under the hood). Return `{contact, experience: [...], skills,
      education}`. If extraction can't recover structured fields, the PDF will
      fail real ATS parsers — fail loud.
- [ ] `extract_jd_requirements(jd_text) -> {must_have: [...], nice_to_have: [...]}`
      — regex pass for obvious skill keywords, plus one Claude call to validate
      and normalize (e.g. "Node" → "Node.js"). Cache the result by JD hash.
- [ ] `keyword_coverage(cv_text, jd_requirements) -> {score: 0.0-1.0, matched, missing}`
      — token-level matching with stemming + synonym list. Score = matched /
      total weighted by must_have vs nice_to_have.
- [ ] `format_audit(pdf_path) -> [issue]` — flag known ATS killers: multi-column
      layout, tables, text-in-images, non-standard fonts, header/footer text,
      hyperlinks-without-fallback-text. Your LaTeX template (`templates/cv.tex`)
      is probably fine; this proves it.
- [ ] `ats_score(cv_pdf, jd_text) -> {parseable: bool, coverage: float, format_issues: [...], overall: float}`
      — composite. This is the function the tailoring loop will optimize against.
- [ ] Tests in `tests/test_ats.py` against the existing PDFs in `data/work/*.json`.
      At least one positive case (current eBay tailored CV) and one engineered
      negative case (multi-column template).

**Acceptance:** `python -m jobpilot.ats <pdf> <jd.txt>` prints a score and a
list of concrete issues. No Claude call required for the parseability + format
audit (only for JD requirement extraction).

### 2. Wire the auto-tailor loop against the ATS score

- [ ] In `src/jobpilot/llm.py:753` (`auto_tailor_loop`), replace the
      `would_shortlist` exit condition with `ats_score.overall >= 0.80`
      (configurable threshold, start at 0.80).
- [ ] Cap iterations at 3. On each iteration, pass the previous run's `missing`
      keywords back into `tailor_cv` as a prompt hint ("prioritize surfacing
      these skills if truthfully present in master_cv or stories: [...]").
- [ ] Plug it into `tailor_resume_node` in `src/jobpilot/agents.py:150`,
      replacing the current single-shot tailor.
- [ ] Remove the `review_tailored` interrupt (`agents.py:212`) from the headless
      path. Keep an `auto_approve: bool` config flag — when False, jobs land in
      the review queue instead of being approved.

**Acceptance:** Running the pipeline end-to-end for a single job reaches
`render_pdfs` without any Streamlit interaction, and the tailored CV scores ≥
0.80 on the ATS simulator OR the job is flagged `needs_review`.

### 3. Merge jobradar into jobpilot/discovery

- [ ] Create `src/jobpilot/discovery/` package.
- [ ] Move `bot.py:fetch_greenhouse / fetch_lever / fetch_ashby /
      fetch_smartrecruiters` into `discovery/sources/ats.py`.
- [ ] Move `bot.py` filter functions (location gate, AI/ML keyword match,
      seniority gate at lines 259-267) into `discovery/filters.py`. Make
      location and seniority configurable from `data/profile.json` instead of
      hardcoded.
- [ ] Replace the JSON-file handoff (`jobs.db → pipeline_jobs.json`). Both
      sides now read/write the same SQLite at `data/jobs.db` — schema:
      `jobs(id, source, url, title, company, location, jd_text, discovered_at,
      seen_at, score, status)`.
- [ ] Port the GH Actions cron from `dublin_ai_jobs_bot/.github/workflows/scan.yml`.
      Schedule: every 30 min for discovery only; tailoring runs once daily on
      a separate schedule.
- [ ] Delete `companies.yaml` duplication — single config in
      `data/discovery_sources.yaml`.
- [ ] Once green for a week, archive `~/code/dublin_ai_jobs_bot` (don't delete —
      keep as reference for a month).

**Acceptance:** Discovery cron writes new jobs to `data/jobs.db`. The pipeline
reads from the same DB. No more JSON-file handoff between projects.

### 4. Daily digest email + review queue

- [ ] Wire up the existing untracked `src/jobpilot/notify.py` (verify it works,
      add tests).
- [ ] New `digest.py`: query `jobs.db` for `status=ready_to_send` from the last
      24h, render an email with top N jobs (rank by `ats_score.overall`).
      Include for each: company, role, ATS score, top matched/missing keywords,
      a one-click link into the Streamlit approval view.
- [ ] Streamlit `app.py`: collapse the existing per-job step-through into a
      single "Review Queue" tab — list of jobs, click to expand, approve/reject
      buttons. The other tabs stay (Add Content, Story Bank, Applications).
- [ ] Cron entry: `jobpilot digest --send` runs once per morning.

**Acceptance:** One email per morning with a ranked list. Approving a job in
Streamlit moves it to `status=approved` and surfaces the "Open + Copy" bundle
(see week-2 item below).

### 5. Prompt caching

- [ ] Migrate the 3 hot LLM functions from `claude -p` CLI to the Anthropic
      Python SDK with `cache_control: ephemeral`:
  - `tailor_cv` (`llm.py:319`) — cache the master_cv prefix
  - `evaluate_cv` (`llm.py:627`) — cache the rubric + master_cv
  - `classify_role_level` (`llm.py:?`) — cache the rubric
- [ ] Keep `_call_claude` for one-off calls; add a new `_call_claude_cached`
      that accepts `cached_prefix` and `dynamic_suffix` separately.
- [ ] Add cache hit telemetry to logs so you can verify the savings.

**Acceptance:** Per-job cost drops measurably (target: 60-70% reduction on the
big-context calls). Cache hit rate visible in logs.

---

## TODO — week 2 (compounding wins)

### 6. Wire in `gaps.py`

- [ ] `gaps.py` already exists untracked (~186 lines, tests in `test_gaps.py`).
      Read it, verify it still makes sense, commit it.
- [ ] Add a `jobpilot gaps` CLI command that aggregates `missing` keywords
      across the last 50 evaluated jobs and ranks by frequency.
- [ ] Split output into two buckets:
  - **Truthful additions** — Sophie has the skill, didn't surface it.
    Suggest stories or master_cv edits.
  - **Real gaps** — skill genuinely absent. Feed into the learning plan node
    that already exists in the graph (`learning_plan_node`).
- [ ] Surface the top 5 gaps in the daily digest email.

### 7. "Open + Copy" application bundle (not auto-fill)

Auto-fill via Playwright is a tarpit (per-portal fragility, captchas, Workday
actively detects bots). Do the boring 80/20 instead:

- [ ] In Streamlit's review queue, an "Open Application" button:
  - Opens the JD URL in the default browser
  - Copies the tailored CV PDF path to clipboard (slot 1)
  - Copies the cover letter text to clipboard (slot 2 — use a clipboard
    history tool like Maccy, or just queue them)
  - Logs the submission timestamp + sets `status=submitted`
- [ ] Goal: 5 minutes per application instead of 20.
- [ ] Defer real auto-submit indefinitely unless one specific portal becomes a
      dominant bottleneck.

### 8. Rejection pattern analysis

- [ ] When Sophie marks a submitted application as `rejected` (or it goes 14
      days with no response), feed the (CV, JD, ATS score, outcome) tuple into
      a weekly review.
- [ ] Look for patterns: are rejected jobs always below a certain score? Do
      certain companies always reject regardless of score? Is the 0.80
      threshold actually predictive, or should it be 0.85?
- [ ] Use this to recalibrate the threshold over time. This is the feedback
      loop that turns the system from "guessing" to "learning".

---

## Deferred (not week 1 or 2)

- LaTeX → weasyprint migration. Real but not urgent; LaTeX works.
- Messaging integration (WhatsApp/Telegram). Nice-to-have. Daily email is
  enough for now.
- Agentic Compliance Orchestrator — separate project, separate ambition.
  Don't conflate.
- Browser auto-submit beyond the "open + copy" bundle.

---

## Things to push back on / decisions that are still open

1. **Threshold for "ready_to_send"**: starting at ATS score ≥ 0.80, but this
   needs calibration once you have ~30 submitted applications with outcomes.
2. **Headless cron vs. once-daily manual run**: the design assumes you *want*
   a fully unattended pipeline. If you actually prefer to drive it daily and
   just want it faster, the `interrupt()` removal is less important than the
   ATS simulator.
3. **Anthropic SDK migration vs staying on `claude -p`**: SDK gives prompt
   caching telemetry but adds an API key dependency. CLI is current setup.
   Decide based on whether the Claude Code subscription supports cached calls
   transparently — if it does, no migration needed.
4. **Where does the discovery DB live**: SQLite at `data/jobs.db` is simple
   but not multi-writer safe if both the cron and the Streamlit UI write to it.
   If concurrency bites, switch to Postgres or just serialize all writes
   through a single process.

## Done-when

The redesign is done when:
- One repo, no JSON-file handoffs.
- A job goes from "discovered by cron" to "in tomorrow's digest email" with
  zero human input and a measured ATS score.
- The auto-tailor loop converges in ≤3 iterations or honestly flags the job
  as "below threshold, needs your judgment".
- Sophie's morning routine: open email, click 3-5 "Open + Copy" buttons,
  paste-and-submit. Total time: under 30 minutes.
