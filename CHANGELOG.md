# Changelog

All notable changes to this project will be documented in this file.

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `jobpilot tailor` subcommand: per-job manual workflow (tailor + cover letter + render + page-count check) outside the LangGraph batch flow
- Three CV variants: `grad` / `tech_eng` / `regtech`, with automatic page-count check
- Identity framing rules: engineer-first summary, role-level-aware framing
- `my_assistant` project entry + recovered content from older CV revisions
- Truthful skill additions to master CV + GitHub Actions CI
- Tier-2 LinkedIn discovery via opencli, wired into the discover CLI
- Tier-1 ATS discovery: poll target companies for Dublin jobs
- `jobpilot gaps` CLI: aggregate ATS keyword gaps across job descriptions
- FastAPI backend exposing pipeline data
- ATS simulator driving the auto-tailor loop
- Auto-tailor loop, scheduled search, architecture redesign
- (WIP) Skill gap analysis, Telegram notifications, planning docs

### Changed
- Made repo public: separated PII into `.example.` data files; CI bootstraps real files from examples before tests
- Narrowed `target_roles` to 4 data-validated Ireland queries; raised search budget to 20
- CI: install `lmodern` font, opt into Node 24

## [0.1.0] — 2026-03-01

### Added
- Initial commit
