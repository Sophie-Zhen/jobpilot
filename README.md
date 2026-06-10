# jobpilot

A personal AI job-search copilot that runs the repetitive parts of a tech job
hunt end-to-end: it discovers roles, tailors a CV and cover letter per job
against a verified master profile, scores them the way a recruiter actually
reads them, renders ATS-friendly PDFs, surfaces who in your network can refer
you, and tracks every application — with a human checkpoint before anything
leaves your machine.

Built for one demanding user (an AI/NLP engineer job-hunting in Dublin on a
work visa), so the design bias is **leverage and correctness over breadth**:
never fabricate a credential, always put a human in front of an outward action,
and compound effort over time.

## What makes it interesting

- **Claude Code CLI as the entire LLM backend.** Every model call goes through
  `claude -p --output-format json` over stdin — no OpenAI dependency, no API
  keys beyond a Claude Code subscription. The orchestration, prompting and JSON
  contracts live in this repo.
- **Anti-fabrication by construction.** `data/master_cv.json` is the single
  source of truth for all career content. Tailoring only produces *micro-
  adjustments* (which bullets to select, summary phrasing, skill ordering,
  per-role technology lines validated against a master skill allow-list) — it
  never rewrites experience from scratch, so it structurally cannot invent a
  job, a number, or a tool you don't have.
- **Role- and framing-aware tailoring.** A 3-variant framing lens
  (`grad` / `tech_eng` / `regtech`) composes with a 4-level role classifier
  (graduate / junior / mid / senior). A career-changer graduate CV leads with
  projects and skills; a senior CV leads with leadership and scale.
- **An honest take on "ATS".** Tech ATSes don't auto-reject on keywords — that's
  a myth. So the relevance checker (`ats.py`) is framed as a proxy for the
  human 7-second scan: keyword coverage **plus a keyword-stuffing penalty**
  (recruiters penalize stuffing), PDF parseability, and a format audit.
- **A second, independent recruiter pass.** `evaluate_cv` scores each tailored
  CV on a Yes/Maybe/No first-scan pile, career trajectory, and weak-bullet
  detection (passive voice / missing numbers / generic phrasing) — and feeds
  the gaps back into the tailoring loop.
- **Referrals as a first-class signal.** The biggest lever in a tech job search
  (and the one that jumps the "local candidates first" queue for a visa
  candidate) is a warm intro. From a LinkedIn `Connections.csv` export (manual,
  no scraping), jobpilot finds who you know at each company, **boosts those
  jobs in scoring**, and leads the Telegram digest card with an "ask for a
  referral before applying" call-to-action.
- **Lives where the work happens.** A Telegram bot pushes daily digest cards
  with Save / Skip / Tailor / Applied buttons; a Gmail inbox-sync reads
  application replies, classifies them (rejection / interview / info request /
  ack) with an LLM, and updates the tracker — all driven by `launchd` on a
  schedule. The pipeline never auto-applies and never auto-DMs.

## Architecture

Single source of truth, per-job working state, persistent tracking:

```
data/master_cv.json      ← all verified career content (gitignored PII)
data/profile.json        ← search config only (roles, locations, exclusions)
data/stories.json        ← supplementary story bank
data/pipeline_jobs.json  ← discovered/scored jobs
data/work/{job_id}.json  ← per-job CV + cover letter + evaluation
data/applications.json   ← application tracking with status history
```

The core flow is a 14-node **LangGraph** pipeline with a human review interrupt:

```
load_profile → load_stories → search_jobs → score_jobs → review_jobs (interrupt)
  → fetch_jds → tailor_resume → evaluate_cv → render_pdfs → apply_jobs
```

A separate learning line analyses skill gaps across job descriptions to suggest
what to study next. In scheduled (cron/launchd) mode the review interrupts are
skipped and scored jobs are saved for later review.

## Capabilities

| Area | What it does |
|------|--------------|
| **Discovery** | Tiered: direct ATS endpoints (Greenhouse/Lever/Ashby) for target companies + LinkedIn via opencli, plus API providers (jsearch, linkedin, active_jobs_db, arbeitnow, remotive). Dedups against seen + applied jobs. |
| **Tailoring** | Per-job CV + cover letter from the master profile; role/variant-aware; LaTeX → PDF with ATS-safe formatting. |
| **Scoring** | Skill overlap + keyword/role match, with a referral boost for companies where you have a connection. |
| **Evaluation** | ATS relevance proxy (`ats.py`) + independent recruiter-scan scorer; weak-bullet feedback loops into re-tailoring. |
| **Referrals** | `Connections.csv` → who can refer you, per job and cross-referenced against your target-company list. |
| **Tracking** | `applications.json` with status history; Gmail inbox-sync auto-updates it and pushes Telegram alerts on status changes. |
| **Interfaces** | CLI, a Telegram bot (digest cards + commands), and a Streamlit web UI. |

## Tech stack

- **Python 3.11**, **LangGraph** for orchestration
- **Claude Code CLI** for all LLM calls (no other model dependency)
- **LaTeX (pdflatex)** + **Jinja2** for PDF generation
- **Pydantic** data models, **Streamlit** web UI
- **python-telegram-bot** for the bot; **Gmail API** (OAuth per account) for inbox-sync
- **launchd** UserAgents for scheduled discovery / digest / inbox-sync
- 275 tests (LLM and network paths mocked, so the suite runs offline)

## Quick start

Requires the [Claude Code CLI](https://claude.com/claude-code) and `pdflatex`
(`brew install --cask mactex-no-gui` on macOS).

```bash
conda create -n jobpilot python=3.11 -y
conda activate jobpilot
pip install -e .

cp .env.example .env   # optional: Telegram + RapidAPI keys
```

Initialise your personal data files from the shipped templates (the real files
are git-ignored, so your data stays private):

```bash
cd data
for f in master_cv master_cover_letter stories profile target_companies; do
  cp "$f.example.json" "$f.json"
done
cd ..
```

Edit each `data/<name>.json` with your own content, then run the pipeline:

```bash
jobpilot run          # discover → score → review → tailor → render → track
jobpilot tailor --job-id <id>   # tailor one job + ATS/recruiter evaluation
jobpilot referrals --targets    # which target companies you have a warm path to
jobpilot status                 # application tracker
```

Other entry points: `streamlit run src/jobpilot/app.py` (web UI),
`jobpilot bot run` (Telegram daemon), `jobpilot inbox-sync` (Gmail → tracker).

## Project structure

- `src/jobpilot/graph.py` — LangGraph wiring (14 nodes)
- `src/jobpilot/agents.py` — node logic (search, score, review, tailor, evaluate, render, apply, learning)
- `src/jobpilot/llm.py` — all LLM functions (tailoring, cover letters, role classification, recruiter evaluation, story structuring)
- `src/jobpilot/ats.py` — relevance + parseability scoring (keyword coverage, stuffing penalty, format audit)
- `src/jobpilot/referrals.py` — referral discovery from a LinkedIn connections export
- `src/jobpilot/job_sources.py` / `discovery/` — job providers + tiered discovery
- `src/jobpilot/renderer.py` + `templates/` — LaTeX escaping + PDF rendering
- `src/jobpilot/cli.py` — CLI; `bot.py` — Telegram bot; `inbox_sync.py` — Gmail tracker sync
- `src/jobpilot/app.py` — Streamlit UI
- `infra/launchd/` — scheduling templates

## Design boundaries

jobpilot deliberately keeps a human in the loop for anything outward-facing: it
drafts but never auto-submits applications, and never automates LinkedIn or
sends DMs. It reads email read-only and never replies. The point is to remove
the repetitive labour, not the judgement.
