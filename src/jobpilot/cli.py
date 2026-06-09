from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

from langgraph.types import Command

from jobpilot.config import load_settings
from jobpilot.graph import build_graph
from jobpilot.profile import ensure_profile_file
from jobpilot.stories import Story, StoryBank
from jobpilot.storage import InMemoryStore, SupabaseStore


def _select_store():
    settings = load_settings()
    if settings.supabase_url and settings.supabase_anon_key:
        return SupabaseStore(settings.supabase_url, settings.supabase_anon_key)
    return InMemoryStore()


def _print_jobs_for_review(payload: dict) -> None:
    print("\nPending review jobs:")
    for job in payload.get("jobs", []):
        print(f"  {job['id']} | {job['title']} | {job['company']} | score={job['score']}")


def _print_tailored_for_review(payload: dict) -> None:
    print(f"\nTailored content for: {payload.get('job_title', '?')} at {payload.get('company', '?')}")
    print("-" * 60)
    if payload.get("cv_summary"):
        print(f"\nSummary:\n  {payload['cv_summary']}")
    for exp in payload.get("cv_experience", []):
        print(f"\n  {exp.get('title', '')}:")
        for bullet in exp.get("bullets", []):
            print(f"    - {bullet}")
    if payload.get("cv_skills"):
        print(f"\nSkills: {', '.join(payload['cv_skills'])}")
    if payload.get("cover_letter_text"):
        print(f"\nCover Letter:\n{payload['cover_letter_text']}")
    if payload.get("selected_stories"):
        print(f"\nStories used: {', '.join(payload['selected_stories'])}")
    print("-" * 60)


def _handle_review_jobs_interrupt(payload: dict) -> dict:
    _print_jobs_for_review(payload)
    raw_ids = input("\nEnter approved job IDs (comma separated): ").strip()
    notes = input("Any notes for this review? ").strip()
    approved_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
    return {"approved_job_ids": approved_ids, "notes": notes}


def _handle_review_tailored_interrupt(payload: dict) -> dict:
    _print_tailored_for_review(payload)
    choice = input("\n[a]pprove / [e]dit / [r]eject? ").strip().lower()

    if choice.startswith("e"):
        content = json.dumps(payload, indent=2, ensure_ascii=False)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="jobpilot_review_", delete=False
        ) as f:
            f.write(content)
            tmp_path = f.name
        editor = os.environ.get("EDITOR", "nano")
        try:
            subprocess.run([editor, tmp_path], check=True)
            edited = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
            return {"action": "edit", "edits": edited}
        except Exception as exc:
            print(f"Edit failed: {exc}. Treating as reject.")
            return {"action": "reject"}
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    if choice.startswith("r"):
        return {"action": "reject"}

    return {"action": "approve"}


def _handle_review_evaluation_interrupt(payload: dict) -> dict:
    score = payload.get("overall_score", "?")
    shortlist = payload.get("would_shortlist", False)
    print(f"\n=== CV Evaluation (Score: {score}/10) ===")
    print(f"Would shortlist: {'Yes' if shortlist else 'No'}")

    if payload.get("red_flags"):
        print("\nRed Flags:")
        for flag in payload["red_flags"]:
            print(f"  ! {flag}")

    if payload.get("keyword_coverage", {}).get("missing_critical"):
        print(f"\nMissing critical keywords: {', '.join(payload['keyword_coverage']['missing_critical'])}")

    if payload.get("strengths"):
        print("\nStrengths:")
        for s in payload["strengths"]:
            print(f"  + {s}")

    if payload.get("suggestions"):
        print("\nSuggestions:")
        for s in payload["suggestions"]:
            print(f"  > {s}")

    choice = input("\n[f]inalize / [r]evise? ").strip().lower()
    if choice.startswith("r"):
        return {"action": "revise", "edits": {}}
    return {"action": "finalize"}


def run_flow(args: argparse.Namespace) -> None:
    settings = load_settings()
    scheduled = getattr(args, "scheduled", False)
    if scheduled:
        settings.scheduled = True
    store = _select_store()
    app = build_graph(settings, store)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {"search_query": "", "variant": getattr(args, "variant", "tech_eng")}
    result = app.invoke(initial_state, config=config)

    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        interrupt_type = payload.get("type", "review_jobs")

        if interrupt_type == "review_tailored":
            resume_data = _handle_review_tailored_interrupt(payload)
        elif interrupt_type == "review_evaluation":
            resume_data = _handle_review_evaluation_interrupt(payload)
        else:
            resume_data = _handle_review_jobs_interrupt(payload)

        result = app.invoke(Command(resume=resume_data), config=config)

    print("\nRun completed.\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def init_profile(args: argparse.Namespace) -> None:
    settings = load_settings()
    path = ensure_profile_file(settings)
    print(f"Profile file ready: {path}")
    print("Edit this file with your real target roles, skills, and keywords.")


# --- Story commands ---


def story_add(args: argparse.Namespace) -> None:
    bank = StoryBank()
    quick_text = getattr(args, "quick", None)

    if quick_text:
        try:
            from jobpilot.llm import structure_story

            story = structure_story(quick_text)
            bank.add_story(story)
            print(f"Story saved: {story.id} — {story.title}")
        except Exception as exc:
            print(f"LLM structuring failed: {exc}")
            print("Adding as raw story instead.")
            story = Story(title=quick_text[:80], situation=quick_text, source="manual")
            bank.add_story(story)
            print(f"Story saved: {story.id} — {story.title}")
    else:
        title = input("Title: ").strip()
        situation = input("Situation (context/problem): ").strip()
        action = input("Action (what you did): ").strip()
        result = input("Result (outcome/impact): ").strip()
        tags_raw = input("Tags (comma separated): ").strip()
        skills_raw = input("Skills (comma separated): ").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
        story = Story(
            title=title,
            situation=situation,
            action=action,
            result=result,
            tags=tags,
            skills=skills,
            source="manual",
        )
        bank.add_story(story)
        print(f"Story saved: {story.id} — {story.title}")


def story_list(args: argparse.Namespace) -> None:
    bank = StoryBank()
    tag = getattr(args, "tag", None)
    skill = getattr(args, "skill", None)
    stories = bank.list_stories(tag=tag, skill=skill)
    if not stories:
        print("No stories found.")
        return
    for s in stories:
        tags_str = ", ".join(s.tags) if s.tags else ""
        print(f"  {s.id} | {s.title} | tags=[{tags_str}] | source={s.source}")


def story_import(args: argparse.Namespace) -> None:
    print("Paste your text (CV bullets, project descriptions, etc.).")
    print("Enter an empty line when done:")
    lines = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    raw_text = "\n".join(lines)
    if not raw_text.strip():
        print("No text provided.")
        return

    try:
        from jobpilot.llm import import_stories

        stories = import_stories(raw_text)
        bank = StoryBank()
        for story in stories:
            bank.add_story(story)
            print(f"  Imported: {story.id} — {story.title}")
        print(f"\n{len(stories)} stories imported.")
    except Exception as exc:
        print(f"Import failed: {exc}")


def story_migrate(args: argparse.Namespace) -> None:
    bank = StoryBank()
    if bank.list_stories():
        print(f"Story bank already has {len(bank.list_stories())} stories. Skipping migration.")
        return

    from jobpilot.storage import InMemoryStore

    legacy_store = InMemoryStore()
    raw_stories = legacy_store._get_legacy_stories()
    if not raw_stories:
        print("No legacy stories to migrate.")
        return

    try:
        from jobpilot.llm import migrate_legacy_stories

        structured = migrate_legacy_stories(raw_stories)
        for story in structured:
            bank.add_story(story)
            print(f"  Migrated: {story.id} — {story.title}")
        print(f"\n{len(structured)} stories migrated to data/stories.json")
    except Exception as exc:
        print(f"LLM migration failed ({exc}). Migrating with basic structure.")
        for raw in raw_stories:
            story = Story(
                title=raw.get("title", "Untitled"),
                situation=raw.get("content", ""),
                tags=raw.get("tags", []),
                skills=raw.get("tags", []),
                source="migration",
            )
            bank.add_story(story)
            print(f"  Migrated (basic): {story.id} — {story.title}")
        print(f"\n{len(raw_stories)} stories migrated (basic format).")


def story_edit(args: argparse.Namespace) -> None:
    bank = StoryBank()
    story = bank.get_story(args.story_id)
    if not story:
        print(f"Story not found: {args.story_id}")
        return

    content = json.dumps(story.model_dump(), indent=2, ensure_ascii=False)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="jobpilot_story_", delete=False
    ) as f:
        f.write(content)
        tmp_path = f.name

    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.run([editor, tmp_path], check=True)
        edited_data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
        edited_story = Story(**edited_data)
        edited_story.id = story.id
        bank.update_story(edited_story)
        print(f"Story updated: {story.id} — {edited_story.title}")
    except Exception as exc:
        print(f"Edit failed: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def story_delete(args: argparse.Namespace) -> None:
    bank = StoryBank()
    story = bank.get_story(args.story_id)
    if not story:
        print(f"Story not found: {args.story_id}")
        return
    confirm = input(f"Delete '{story.title}'? [y/N] ").strip().lower()
    if confirm == "y":
        bank.delete_story(args.story_id)
        print(f"Deleted: {args.story_id}")
    else:
        print("Cancelled.")


def story_refine(args: argparse.Namespace) -> None:
    bank = StoryBank()
    story = bank.get_story(args.story_id)
    if not story:
        print(f"Story not found: {args.story_id}")
        return
    try:
        from jobpilot.llm import refine_story

        refined = refine_story(story, args.correction)
        bank.update_story(refined)
        print(f"Story refined: {refined.id} — {refined.title}")
    except Exception as exc:
        print(f"Refine failed: {exc}")


# --- Application tracking commands ---


def app_status(args: argparse.Namespace) -> None:
    apps_path = Path("data/applications.json")
    if not apps_path.exists():
        print("No applications tracked yet.")
        return
    apps = json.loads(apps_path.read_text(encoding="utf-8"))
    if not apps:
        print("No applications tracked yet.")
        return
    for app in apps:
        print(
            f"  {app.get('job_id', '?')} | {app.get('title', '?')} | "
            f"{app.get('company', '?')} | status={app.get('status', '?')}"
        )


def app_update(args: argparse.Namespace) -> None:
    apps_path = Path("data/applications.json")
    if not apps_path.exists():
        print("No applications tracked yet.")
        return
    apps = json.loads(apps_path.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    found = False
    for app in apps:
        if app.get("job_id") == args.job_id:
            app["status"] = args.status
            history = app.setdefault("status_history", [])
            history.append({
                "status": args.status,
                "date": today,
                "notes": getattr(args, "notes", "") or "",
            })
            dates = app.setdefault("dates", {})
            if args.status not in dates or dates.get(args.status) is None:
                dates[args.status] = today
            found = True
            break
    if not found:
        print(f"Application not found: {args.job_id}")
        return
    apps_path.write_text(
        json.dumps(apps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Updated {args.job_id} → status={args.status}")


def inbox_sync(args: argparse.Namespace) -> None:
    from jobpilot.inbox_sync import run_inbox_sync

    summary = run_inbox_sync(
        query=args.query,
        dry_run=args.dry_run,
        account_filter=args.account,
        push_telegram=not args.no_telegram,
        non_interactive=args.non_interactive,
    )
    # Surface auth failures to the shell so launchd marks the run as failed
    # and Sophie can grep her log for the actual error.
    if summary.get("auth_failures"):
        sys.exit(1)


def app_feedback(args: argparse.Namespace) -> None:
    apps_path = Path("data/applications.json")
    if not apps_path.exists():
        print("No applications tracked yet.")
        return
    apps = json.loads(apps_path.read_text(encoding="utf-8"))
    today = date.today().isoformat()
    found = False
    for app in apps:
        if app.get("job_id") == args.job_id:
            app["status"] = args.response
            history = app.setdefault("status_history", [])
            history.append({
                "status": args.response,
                "date": today,
                "notes": getattr(args, "notes", "") or "",
            })
            dates = app.setdefault("dates", {})
            dates["response"] = today
            app["feedback"] = {
                "response_type": args.response,
                "date": today,
                "notes": getattr(args, "notes", "") or "",
            }
            found = True
            break
    if not found:
        print(f"Application not found: {args.job_id}")
        return
    apps_path.write_text(
        json.dumps(apps, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Recorded: {args.job_id} → {args.response}")


# --- Main CLI ---


def gaps_scan(args: argparse.Namespace) -> None:
    """Aggregate ATS keyword gaps across data/work/*.json work files."""
    from pathlib import Path
    from jobpilot.gaps import (
        scan_ats_gaps,
        recompute_ats_for_stale,
        _master_cv_searchable_text,
    )

    work_dir = Path("data/work")
    if not work_dir.exists():
        print(f"No work directory at {work_dir}. Run the pipeline on at least one job first.")
        return

    if args.recompute:
        print("Recomputing ATS scores for stale work files (this calls Claude — may take several minutes)...")
        n = recompute_ats_for_stale(
            work_dir,
            force=args.force,
            progress_cb=lambda m: print(f"  {m}"),
        )
        print(f"Recomputed {n} file(s).\n")

    master_cv_text = _master_cv_searchable_text()
    result = scan_ats_gaps(work_dir, master_cv_text=master_cv_text)

    must = result["missing_must"]
    nice = result["missing_nice"]

    if args.format == "json":
        import json as _json
        print(_json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not must and not nice:
        print("No ATS gap data found. Either no work files have an ats_score yet,")
        print("or run with --recompute to backfill.")
        return

    _LABELS = {
        "in_master": "✓ in master",
        "partial":   "~ partial   ",
        "absent":    "✗ absent    ",
    }

    def _row(entry: dict, total: int) -> str:
        label = _LABELS.get(entry["annotation"], entry["annotation"])
        return f"  {entry['frequency']:>2}/{total:<2}  {entry['skill']:<30s}  [{label}]"

    # Determine total job-count for the denominator
    all_job_ids: set[str] = set()
    for e in must + nice:
        for j in e["jobs"]:
            all_job_ids.add(j["id"])
    total_jobs = len(all_job_ids)

    print(f"=== Top missing must-haves (across {total_jobs} jobs with ATS data) ===")
    if must:
        for entry in must[: args.top]:
            print(_row(entry, total_jobs))
    else:
        print("  (none)")

    print()
    print(f"=== Top missing nice-to-haves ===")
    if nice:
        for entry in nice[: args.top]:
            print(_row(entry, total_jobs))
    else:
        print("  (none)")

    # Action summary: bucket by annotation
    in_master = [e for e in must if e["annotation"] == "in_master"]
    partial = [e for e in must if e["annotation"] == "partial"]
    absent = [e for e in must if e["annotation"] == "absent"]
    print()
    print("=== Action summary (must-haves only) ===")
    print(f"  ✓ in master ({len(in_master)}): already in master_cv text — fix tailoring to surface them")
    print(f"  ~ partial   ({len(partial)}): a token appears — tighten bullets to use exact phrasing")
    print(f"  ✗ absent    ({len(absent)}): not in master_cv — your judgment: truthful add OR real learning gap")

    if in_master:
        print()
        print("Quickest wins (already in master, just need surfacing):")
        for entry in in_master[:5]:
            print(f"  - {entry['skill']} (missing in {entry['frequency']} jobs)")
    if absent:
        print()
        print("Highest-frequency absent (consider adding from stories or filtering these roles):")
        for entry in absent[:8]:
            print(f"  - {entry['skill']} (missing in {entry['frequency']} jobs)")


def search_jobs(args: argparse.Namespace) -> None:
    """Run a job search using API-based sources. Suitable for cron scheduling."""
    from jobpilot.config import load_settings
    from jobpilot.profile import load_profile
    from jobpilot.job_sources import scheduled_search

    settings = load_settings()
    profile = load_profile(settings)
    result = scheduled_search(settings, profile)

    if result.get("error"):
        print(f"Error: {result['error']}")
        sys.exit(1)

    print(f"Search complete: {result['found_today']} found, {result['new']} new, {result['total']} total in pipeline")
    print(f"  Query: {result.get('query', '?')} | {result.get('search_info', '')}")


def referrals_cmd(args: argparse.Namespace) -> None:
    """Surface 1st-degree LinkedIn connections for referrals (book ch.2, the 10x lever).

    With a company arg: who could refer you there. Without: your top companies by
    connection count — networking targets to deepen.
    """
    from jobpilot.config import load_settings
    from jobpilot.referrals import find_referrers, load_connections, top_companies

    csv_path = load_settings().connections_csv
    conns = load_connections(csv_path)
    if not conns:
        print(f"No connections loaded from {csv_path}.")
        print("Export Connections.csv from LinkedIn (Settings → Data Privacy → Get a")
        print("copy of your data → Connections) and save it there (or set CONNECTIONS_CSV).")
        return

    if getattr(args, "targets", False):
        from jobpilot.referrals import cross_reference_targets, load_target_companies

        targets = load_target_companies()
        if not targets:
            print("No target_companies.json found (data/target_companies.json).")
            return
        matches = cross_reference_targets(targets, conns)
        if not matches:
            print(f"No warm connections among your {len(targets)} target companies yet.")
            return
        print(f"{len(matches)}/{len(targets)} target companies have a warm connection:\n")
        for t, refs in matches:
            tag = "" if t.status == "active" else " [cold]"
            cluster = f"  [{t.cluster}]" if t.cluster else ""
            who = "; ".join(
                r.name + (f" — {r.position}" if r.position else "") for r in refs[:5]
            )
            print(f"  ✓ {t.name}{tag}{cluster}: {len(refs)} — {who}")
        return

    company = " ".join(args.company).strip() if args.company else ""
    if company:
        refs = find_referrers(company, conns)
        if not refs:
            print(f"No connections at '{company}' among {len(conns)} contacts.")
            return
        print(f"{len(refs)} connection(s) at {company} — ask BEFORE applying:")
        for r in refs:
            pos = f" — {r.position}" if r.position else ""
            url = f"  {r.url}" if r.url else ""
            print(f"  • {r.name}{pos}{url}")
        return

    print(f"{len(conns)} connections loaded. Top companies (networking targets):")
    for name, count in top_companies(conns, n=15):
        print(f"  {count:>3}  {name}")


def discover(args: argparse.Namespace) -> None:
    """Tier-1 (ATS poll) + Tier-2 (opencli LinkedIn) discovery.

    Tier-1: hits every active Tier-1 company in target_companies.json,
    filtered to Dublin/Remote-EMEA. Fast, free, deterministic.

    Tier-2: opencli LinkedIn keyword search using profile.target_roles
    with " Dublin" appended (opencli's --location flag is broken; we
    encode location in the query and post-filter). Rate-limited with
    random inter-call delays. Skipped if --skip-opencli.
    """
    from jobpilot.discovery import discover_all, discover_broad, opencli_available
    from jobpilot.job_sources import (
        load_pipeline_jobs,
        merge_jobs,
        record_seen_jobs,
        save_pipeline_jobs,
    )
    from jobpilot.profile import load_profile
    from jobpilot.config import load_settings

    all_jobs: list[dict] = []
    all_stats: dict = {}

    # ── Tier 1 — direct ATS polling ──
    print("Tier 1: polling ATS endpoints...")
    t1_jobs, t1_stats = discover_all(progress_cb=lambda m: print(m))
    all_jobs.extend(t1_jobs)
    all_stats["tier1"] = t1_stats
    print(f"  Tier 1 total: {t1_stats['total_jobs']} Dublin-eligible across "
          f"{t1_stats['companies_polled']} companies "
          f"({len(t1_stats['errors'])} errors)")

    # ── Tier 2 — opencli LinkedIn keyword search ──
    if args.skip_opencli:
        print("\nTier 2: SKIPPED (--skip-opencli)")
        all_stats["tier2"] = {"skipped": True}
    elif not opencli_available():
        print("\nTier 2: SKIPPED (opencli not installed — run `npm install` in project root)")
        all_stats["tier2"] = {"skipped": True, "reason": "opencli unavailable"}
    else:
        settings = load_settings()
        profile = load_profile(settings)
        target_roles = profile.get("target_roles", [])
        if args.queries:
            queries = [q.strip() for q in args.queries.split(",") if q.strip()]
        else:
            # Geo suffix: Ireland (not just Dublin) — Sophie's filter accepts
            # any Irish location for now; tightening to Dublin-only is a future TODO.
            queries = [f"{role} Ireland" for role in target_roles]

        if not queries:
            print("\nTier 2: SKIPPED (no target_roles in profile)")
        else:
            print(f"\nTier 2: opencli LinkedIn search ({len(queries)} queries, budget={args.budget})")
            t2_jobs, t2_stats = discover_broad(
                queries,
                limit_per_query=args.limit,
                date_posted=args.date_posted,
                budget=args.budget,
                progress_cb=lambda m: print(m),
            )
            print(f"  Tier 2 total: {t2_stats['total_jobs']} Dublin-eligible "
                  f"(daily usage: {t2_stats['daily_used']}/{t2_stats['daily_budget']})")
            all_jobs.extend(t2_jobs)
            all_stats["tier2"] = t2_stats

    print()
    print(f"=== Combined: {len(all_jobs)} Dublin-eligible jobs ===")

    if args.dry_run:
        print("\n--dry-run: not writing to pipeline_jobs.json")
        if args.format == "json":
            import json as _json
            print(_json.dumps({"stats": all_stats, "sample": all_jobs[:5]}, indent=2, ensure_ascii=False))
        return

    existing = load_pipeline_jobs()
    before = len(existing)
    merged = merge_jobs(existing, all_jobs)
    save_pipeline_jobs(merged)
    record_seen_jobs(all_jobs)

    new_count = len(merged) - before
    print(f"\nMerged into data/pipeline_jobs.json: {new_count} new, {len(merged)} total")

    if new_count > 0:
        new_ids = {j["id"] for j in merged[before:]}
        new_jobs = [j for j in all_jobs if j["id"] in new_ids]
        import re as _re
        eng_re = _re.compile(
            r"\b(engineer|developer|scientist|researcher|ml|ai|nlp|llm|"
            r"data|backend|frontend|fullstack|platform|infra|sre|architect)\b",
            _re.IGNORECASE,
        )
        non_eng_re = _re.compile(
            r"\b(solutions engineer|account executive|customer success|bdr|sdr|"
            r"recruiter|marketing|pre-sales|partner|renewal)\b",
            _re.IGNORECASE,
        )
        eng_new = [j for j in new_jobs if eng_re.search(j["title"]) and not non_eng_re.search(j["title"])]
        if eng_new:
            print(f"\n{len(eng_new)} new eng-flavored Dublin jobs to review:")
            for j in eng_new[:20]:
                src = "T1" if j["source"].startswith("ats:") else "T2"
                print(f"  [{src}] [{j['company'][:24]:24}] {j['title'][:55]:55}  →  {j['url']}")


_ENG_TITLE_RE = None  # lazy compiled
_NON_ENG_TITLE_RE = None


def _eng_title_filters():
    """Compile (and cache) the eng / non-eng title regexes used by discover + digest."""
    global _ENG_TITLE_RE, _NON_ENG_TITLE_RE
    if _ENG_TITLE_RE is None:
        import re as _re
        _ENG_TITLE_RE = _re.compile(
            r"\b(engineer|developer|scientist|researcher|ml|ai|nlp|llm|"
            r"data|backend|frontend|fullstack|platform|infra|sre|architect)\b",
            _re.IGNORECASE,
        )
        _NON_ENG_TITLE_RE = _re.compile(
            r"\b(solutions engineer|account executive|customer success|bdr|sdr|"
            r"recruiter|marketing|pre-sales|partner|renewal)\b",
            _re.IGNORECASE,
        )
    return _ENG_TITLE_RE, _NON_ENG_TITLE_RE


def _is_eng_flavored(title: str) -> bool:
    eng, non_eng = _eng_title_filters()
    return bool(eng.search(title)) and not non_eng.search(title)


_RELATIVE_AGE_RE = None


def _job_age_days(job: dict) -> int | None:
    """Resolve a job's age in days, or None if no parseable date.

    Day resolution; <24h returns 0. Handles the three formats currently in
    pipeline_jobs.json:
      - T1 ATS: ``posted_at`` ISO timestamp ("2026-02-17T16:43:23+00:00")
      - opencli: ``posted_at`` ISO date  ("2026-05-16")
      - web_:    ``posted`` relative string ("3 hours ago", "1 day ago")
    """
    from datetime import date as _date, datetime as _dt
    import re as _re
    global _RELATIVE_AGE_RE
    if _RELATIVE_AGE_RE is None:
        _RELATIVE_AGE_RE = _re.compile(
            r"(\d+)\s+(hour|day|week|month|year)s?\s+ago",
            _re.IGNORECASE,
        )

    today = _date.today()
    pa = job.get("posted_at") or ""
    if pa:
        try:
            if "T" in pa:
                dt = _dt.fromisoformat(pa.replace("Z", "+00:00")).date()
            else:
                dt = _date.fromisoformat(pa[:10])
            return max(0, (today - dt).days)
        except (ValueError, TypeError):
            pass

    # Relative "posted" string ("3 hours ago"). This is frozen at scrape
    # time, so we must anchor it to date_found, NOT to today — otherwise
    # an old web_ job from April with posted="1 hour ago" looks fresh now.
    posted = job.get("posted") or ""
    if posted:
        low = posted.lower()
        offset_days: int | None = None
        if any(t in low for t in ("just", "moment", "now")):
            offset_days = 0
        else:
            m = _RELATIVE_AGE_RE.search(posted)
            if m:
                n = int(m.group(1))
                unit = m.group(2).lower()
                offset_days = {"hour": 0, "day": n, "week": n * 7,
                               "month": n * 30, "year": n * 365}.get(unit)
        if offset_days is not None:
            anchor_str = job.get("date_found") or ""
            if anchor_str:
                try:
                    from datetime import timedelta as _td
                    anchor = _date.fromisoformat(anchor_str[:10])
                    real = anchor - _td(days=offset_days)
                    return max(0, (today - real).days)
                except (ValueError, TypeError):
                    pass
            # No anchor: only trust if scrape was recent (best-effort).
            # Without an anchor we can't know, so be conservative → drop.
            return None
    return None


def _format_age(days: int | None) -> str:
    if days is None:
        return "age?"
    if days == 0:
        return "today"
    if days == 1:
        return "1d ago"
    return f"{days}d ago"


def _load_digested(path: Path) -> dict:
    if not path.exists():
        return {"sent_ids": [], "last_run": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("sent_ids", [])
        data.setdefault("last_run", None)
        return data
    except Exception:
        return {"sent_ids": [], "last_run": None}


def _save_digested(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _slug_for_folder(text: str) -> str:
    """Lowercase, non-alphanumeric → underscore, collapse repeats, strip edges."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", (text or "").lower())).strip("_")


def _job_folder(job: dict) -> str:
    """Human-readable per-job output folder name.

    Format: ``{company}_{title_short}_{id_suffix}``. ``title_short`` is the
    first 6 words of the title slugified and capped at 40 chars. ``id_suffix``
    is the trailing numeric portion of ``job_id`` (the platform's posting id,
    e.g. ``7925313`` from ``intercom_7925313``); falls back to the last 8
    chars for opaque schemes. Falls back to the bare ``job_id`` if all
    components slugify to empty.
    """
    job_id = job.get("id", "") or "unknown"
    company_slug = _slug_for_folder(job.get("company", ""))
    title_words = (job.get("title", "") or "").split()[:6]
    title_slug = _slug_for_folder(" ".join(title_words))[:40].rstrip("_")
    m = re.search(r"(\d+)$", job_id)
    id_suffix = m.group(1) if m else job_id[-8:].lstrip("_")
    parts = [p for p in (company_slug, title_slug, id_suffix) if p]
    return "_".join(parts) if parts else job_id


def _format_job_digest(job: dict, connections: list | None = None) -> str:
    """Format one job as a Telegram-friendly plain-text card.

    Plain text (no markdown) — Telegram auto-linkifies the URL and we
    avoid escape collisions on URLs containing underscores.

    If ``connections`` (a parsed LinkedIn Connections list) is passed and the
    candidate knows someone at the company, the card leads with a referral
    call-to-action — a warm path is the ~10x lever (Orosz ch.2) and the advice
    is to ask BEFORE applying, so it goes right under the title.
    """
    company = job.get("company", "?")
    title = job.get("title", "?")
    location = job.get("location", "")
    skills = job.get("skills", []) or []
    summary = job.get("jd_summary") or job.get("description", "") or ""
    if len(summary) > 350:
        summary = summary[:347].rstrip() + "..."

    age = _job_age_days(job)
    age_str = _format_age(age)
    header_prefix = "[NEW] " if age == 0 else ""

    lines = [f"{header_prefix}[{company}] {title}"]
    if connections:
        from jobpilot.referrals import find_referrers

        refs = find_referrers(company, connections)
        if refs:
            shown = ", ".join(
                r.name + (f" ({r.position})" if r.position else "") for r in refs[:3]
            )
            more = f" +{len(refs) - 3} more" if len(refs) > 3 else ""
            lines.append(
                f"🤝 {len(refs)} connection(s) here: {shown}{more} "
                f"— ask for a referral BEFORE applying"
            )
    meta_parts = [p for p in [location, f"Posted {age_str}"] if p]
    lines.append(" · ".join(meta_parts))
    if skills:
        lines.append(f"Skills: {', '.join(skills[:8])}")
    if summary:
        lines.append("")
        lines.append(summary)
    lines.append("")
    lines.append(f"URL: {job.get('url', '?')}")
    lines.append(f"ID:  {job.get('id', '?')}")
    return "\n".join(lines)


def _norm_key(company: str, title: str) -> tuple[str, str]:
    """Normalized (company, title) key for cross-posting dedup.

    Lowercases and reduces non-alphanumerics to single spaces so the same role
    re-posted under a new id (or from another source) collapses to one key —
    e.g. EY "Agentic AI Engineer - Senior Consultant" at two posting ids.
    """
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()
    return norm(company), norm(title)


def digest(args: argparse.Namespace) -> None:
    """Push today's top eng-flavored, un-digested, un-applied jobs to Telegram.

    Filters pipeline_jobs.json by:
      - eng-flavored title (same regex as discover)
      - job_id not in applications.json (skip already-applied)
      - job_id not in skipped.json (skip already-dropped)
      - job_id not in data/digested.json (skip already-sent today/earlier)
      - normalized (company, title) not already applied/rejected/skipped, and
        deduped within the run (same role re-posted under a new id)

    Sends one Telegram message per job (one card per job sets up Phase 2
    inline buttons). Tracks sent ids in data/digested.json. Use --reset
    to clear that file for testing.
    """
    from datetime import datetime
    from jobpilot.notify import send_telegram

    pipeline_path = Path("data/pipeline_jobs.json")
    if not pipeline_path.exists():
        print(f"No pipeline jobs at {pipeline_path}. Run `jobpilot discover` first.")
        return
    jobs = json.loads(pipeline_path.read_text(encoding="utf-8"))

    # Cross-pile dedup: exclude a job if its id OR its normalized
    # (company, title) already appears in applications (any status, incl.
    # rejection) or skipped.json — catches the same role re-posted under a new
    # id. ``seen_keys`` holds the normalized keys; ``applied_ids``/``skipped_ids``
    # the exact ids.
    applied_ids: set[str] = set()
    skipped_ids: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    for path, id_set in ((Path("data/applications.json"), None),
                         (Path("data/skipped.json"), None)):
        if not path.exists():
            continue
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for r in rows:
            jid = r.get("job_id")
            if jid:
                (applied_ids if path.name == "applications.json" else skipped_ids).add(jid)
            seen_keys.add(_norm_key(r.get("company", ""), r.get("title", "")))

    digested_path = Path("data/digested.json")
    if args.reset:
        if digested_path.exists():
            digested_path.unlink()
        print("Cleared data/digested.json.")
        if not args.send:
            return
    digested = _load_digested(digested_path)
    sent_ids: set[str] = set(digested["sent_ids"])

    max_age = args.max_age_days
    eligible: list[tuple[int, dict]] = []
    skipped_no_date = 0
    skipped_too_old = 0
    skipped_dup = 0
    for j in jobs:
        if not j.get("id"):
            continue
        if j["id"] in applied_ids or j["id"] in sent_ids or j["id"] in skipped_ids:
            continue
        if not _is_eng_flavored(j.get("title", "")):
            continue
        if _norm_key(j.get("company", ""), j.get("title", "")) in seen_keys:
            skipped_dup += 1  # same role already applied/rejected/skipped
            continue
        age = _job_age_days(j)
        if age is None:
            skipped_no_date += 1
            continue
        if age > max_age:
            skipped_too_old += 1
            continue
        eligible.append((age, j))

    # Referable jobs first (a warm path is the ~10x lever — ask before applying),
    # then newest within each group (smallest age first) for determinism.
    from jobpilot.config import load_settings
    from jobpilot.referrals import find_referrers, load_connections

    connections = load_connections(load_settings().connections_csv)

    def _has_referrer(job: dict) -> bool:
        return bool(connections and find_referrers(job.get("company", ""), connections))

    eligible.sort(key=lambda t: (not _has_referrer(t[1]), t[0]))
    # Collapse same-role-different-id within this run, keeping the freshest.
    run_seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for _, j in eligible:
        key = _norm_key(j.get("company", ""), j.get("title", ""))
        if key in run_seen:
            skipped_dup += 1
            continue
        run_seen.add(key)
        deduped.append(j)
    picks = deduped[: args.limit]
    print(f"Filter: age≤{max_age}d  "
          f"(skipped {skipped_no_date} undateable, {skipped_too_old} too old, "
          f"{skipped_dup} dup-of-seen)")

    if not picks:
        print("Nothing to digest: 0 eligible jobs after filters.")
        print(f"  pipeline={len(jobs)}  applied={len(applied_ids)}  "
              f"digested={len(sent_ids)}")
        return

    print(f"Eligible: {len(eligible)}  Sending: {len(picks)}  "
          f"(limit={args.limit})\n")

    if args.dry_run:
        for i, job in enumerate(picks, 1):
            print(f"--- card {i}/{len(picks)} ---")
            print(_format_job_digest(job, connections))
            print()
        print("--dry-run: nothing sent to Telegram.")
        return

    sent_now = 0
    failed = 0
    for job in picks:
        message = _format_job_digest(job, connections)
        # parse_mode=None: digest cards embed raw JD descriptions which
        # may contain unbalanced markdown delimiters or HTML entities (e.g.
        # &lt;/&quot;/`*`/`_`) that trigger Telegram 400 "can't parse
        # entities" errors. Cards are formatted as plain text anyway.
        from jobpilot.bot import build_card_markup_dict
        markup = build_card_markup_dict(job["id"])
        ok = send_telegram(message, parse_mode=None, reply_markup=markup)
        if ok:
            sent_now += 1
            digested["sent_ids"].append(job["id"])
            print(f"  ✓ [{job.get('company','?')[:24]:24}] {job.get('title','?')[:50]}")
        else:
            failed += 1
            print(f"  ✗ [{job.get('company','?')[:24]:24}] {job.get('title','?')[:50]}  (send failed)")

    digested["last_run"] = datetime.now().isoformat(timespec="seconds")
    _save_digested(digested_path, digested)
    print(f"\nDigest done: {sent_now} sent, {failed} failed.")
    if failed and not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("Hint: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in env.")


def bot_run(_args: argparse.Namespace) -> None:
    """Start the Telegram bot daemon (long-polling). Blocks until killed."""
    from jobpilot.bot import run_bot
    run_bot()


def cleanup(args: argparse.Namespace) -> None:
    """Delete output folders for skipped / rejected jobs (keeps job metadata).

    See ``jobpilot.cleanup`` for the keep/delete rules. Use ``--dry-run`` to
    preview before deleting.
    """
    from jobpilot.cleanup import sweep

    res = sweep(dry_run=args.dry_run)
    mb = res["bytes_freed"] / 1_000_000
    verb = "Would free" if args.dry_run else "Freed"
    print(f"{verb} {mb:.1f} MB across {res['count']} output folder(s)"
          + (" (dry-run)" if args.dry_run else ""))
    for d in res["deleted"]:
        prefix = "  would delete: " if args.dry_run else "  deleted: "
        print(f"{prefix}{d['folder']}  ({d['bytes'] / 1024:.0f} KB)")
    if not res["deleted"]:
        print("  Nothing to clean — no skipped/rejected jobs have output folders.")


_EVAL_START = "=== EVALUATION ==="
_EVAL_END = "=== END EVALUATION ==="


def _format_tailor_eval(ats_result, evaluation: dict) -> str:
    """Plain-text recruiter-scan verdict for a tailored CV, bracketed by markers.

    The bot extracts the block between the markers to relay it to Telegram, so
    keep the markers stable. Surfaces the book's high-signal feedback: the
    Yes/Maybe/No first-scan pile, the ATS keyword gap, career trajectory, and
    the specific weak bullets to rewrite (passive voice / no number / generic).
    """
    pile = str(evaluation.get("pile", "")).lower()
    pile_icon = {"yes": "✅", "maybe": "🟡", "no": "❌"}.get(pile, "•")
    score = evaluation.get("overall_score", "?")
    shortlist = evaluation.get("would_shortlist")
    shortlist_str = "would shortlist" if shortlist else "would NOT shortlist"

    lines = [_EVAL_START, f"{pile_icon} Pile: {pile.upper() or '?'} ({score}/10) · {shortlist_str}"]

    if ats_result is not None:
        passed = "pass" if ats_result.threshold_passed else "below threshold"
        line = f"ATS: {ats_result.overall:.2f} ({passed})"
        missing = list(ats_result.coverage.missing_must)
        if missing:
            line += f" · missing must-haves: {', '.join(missing[:6])}"
        lines.append(line)

    traj = evaluation.get("trajectory") or {}
    if isinstance(traj, dict) and traj.get("assessment"):
        note = traj.get("note", "")
        lines.append(f"Trajectory: {traj['assessment']}" + (f" — {note}" if note else ""))

    weak = evaluation.get("weak_bullets") or []
    if weak:
        lines.append(f"Weak bullets ({len(weak)}):")
        for w in weak[:5]:
            if isinstance(w, dict):
                lines.append(f"  • {w.get('issue', '?')}: {w.get('bullet', '')}")

    suggestions = evaluation.get("suggestions") or []
    if suggestions:
        lines.append("Top fixes:")
        for s in suggestions[:3]:
            lines.append(f"  • {s}")

    lines.append(_EVAL_END)
    return "\n".join(lines)


def tailor_one_job(args: argparse.Namespace) -> None:
    """Tailor CV + cover letter for a single job from pipeline_jobs.json.

    Per-job manual workflow — bypasses the LangGraph batch pipeline so the
    discovery + scoring steps don't re-run. Outputs PDFs to
    output/{company}_{title}_{id}/cv_{variant}.pdf and cover_letter_{variant}.pdf,
    plus a state JSON for inspection / re-render later. Runs check_page_count
    on the CV and prints any half-page / overflow warning.
    """
    from jobpilot.llm import (
        TARGET_PAGES_BY_VARIANT,
        classify_role_level,
        fetch_full_jd,
        generate_cover_letter,
        tailor_cv,
    )
    from jobpilot.profile import load_profile
    from jobpilot.renderer import check_page_count, render_cover_letter, render_cv

    settings = load_settings()
    profile = load_profile(settings)
    bank = StoryBank()

    pipeline_path = Path("data/pipeline_jobs.json")
    if not pipeline_path.exists():
        print(f"No pipeline jobs at {pipeline_path}. Run `jobpilot discover` first.")
        sys.exit(1)
    jobs = json.loads(pipeline_path.read_text(encoding="utf-8"))

    job: dict | None = None
    if args.job_id:
        for j in jobs:
            if j.get("id") == args.job_id:
                job = j
                break
        if job is None:
            print(f"No job with id={args.job_id!r} in pipeline ({len(jobs)} jobs total).")
            sys.exit(1)
    else:
        idx = args.job_index
        if idx is None or idx < 0 or idx >= len(jobs):
            print(f"--job-index must be in 0..{len(jobs) - 1}")
            sys.exit(1)
        job = jobs[idx]

    job_id = job.get("id", "unknown")
    company = job.get("company", "?")
    title = job.get("title", "?")
    variant = args.variant

    print(f"Tailoring for: [{company}] {title}")
    print(f"  job_id : {job_id}")
    print(f"  variant: {variant}")
    print(f"  url    : {job.get('url', '?')}")
    print()

    # T2 LinkedIn jobs often arrive with a 200-300 char snippet; fetch the full
    # JD so the tailor LLM has real content to work with. T1 ATS jobs already
    # carry ~4k chars from the source API and are skipped.
    description = job.get("full_description") or job.get("description", "")
    if len(description) < 500 and job.get("url"):
        print(f"Description is short ({len(description)} chars). Fetching full JD ...")
        try:
            full = fetch_full_jd(job["url"])
            if full and len(full) > len(description):
                job["full_description"] = full
                description = full
                print(f"  fetched: {len(full)} chars")
        except Exception as exc:
            print(f"  fetch failed: {exc}; proceeding with short description")

    job_text = f"{title} {description}"
    relevant_stories = bank.find_similar(job_text, top_k=8)

    role_level = "graduate" if variant == "grad" else classify_role_level(job)

    print(f"Calling tailor_cv (LLM, ~30-60s)  [role_level={role_level}] ...")
    cv_data = tailor_cv(job, relevant_stories, profile, role_level=role_level, variant=variant)
    cv_data["role_level"] = role_level
    cv_data["variant"] = variant
    cv_data["job_id"] = job_id

    print("Calling generate_cover_letter (LLM, ~30-60s) ...")
    cover_letter_text = generate_cover_letter(job, relevant_stories, profile, role_level=role_level)

    output_dir = Path(settings.output_dir) / _job_folder(job)
    output_dir.mkdir(parents=True, exist_ok=True)

    state_path = output_dir / f"state_{variant}.json"
    state_path.write_text(
        json.dumps(
            {
                "job": job,
                "variant": variant,
                "role_level": role_level,
                "cv_data": cv_data,
                "cover_letter": cover_letter_text,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print("\nRendering CV PDF ...")
    cv_path = render_cv(cv_data, output_dir / f"cv_{variant}.pdf")

    print("Rendering cover letter PDF ...")
    cl_data = {
        "name": cv_data.get("name", ""),
        "date": date.today().strftime("%B %d, %Y"),
        "company": company,
        "job_title": title,
        "body": cover_letter_text,
    }
    cl_path = render_cover_letter(cl_data, output_dir / f"cover_letter_{variant}.pdf")

    target = TARGET_PAGES_BY_VARIANT[variant]
    info = check_page_count(cv_path, target_pages=target)
    fill_pct = round((info["last_page_fill_ratio"] or 0) * 100)
    print(f"\nPage check (variant={variant}, target={target}):")
    print(f"  pages: {info['page_count']}    last-page fill: {fill_pct}%")
    if info["warning"]:
        print(f"  WARN: {info['warning']}")
    else:
        print("  OK — meets target")

    # Recruiter-scan evaluation: the same objective ATS check + 7-second-scan
    # recruiter score the Streamlit loop runs, so the bot/CLI path surfaces the
    # Yes/Maybe/No pile and weak-bullet feedback instead of stopping at a PDF.
    if not args.no_eval:
        from jobpilot.ats import ats_score
        from jobpilot.llm import evaluate_cv

        print("\nEvaluating (ATS + recruiter scan, ~30-60s) ...")
        try:
            ats_result = ats_score(cv_data=cv_data, jd_text=description, pdf_path=cv_path, use_llm=True)
        except Exception as exc:
            print(f"  ATS scoring failed: {exc}")
            ats_result = None
        try:
            evaluation = evaluate_cv(cv_data, job, cover_letter_text)
        except Exception as exc:
            print(f"  Recruiter evaluation failed: {exc}")
            evaluation = {}
        if ats_result is not None or evaluation:
            print()
            print(_format_tailor_eval(ats_result, evaluation))

    print("\nReady to review:")
    print(f"  open {cv_path}")
    print(f"  open {cl_path}")
    print(f"  state: {state_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="JobPilot CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    run_parser = subparsers.add_parser("run", help="Run the job search pipeline")
    run_parser.add_argument("--scheduled", action="store_true", help="Scheduled mode (no interactive review)")
    run_parser.add_argument(
        "--variant",
        choices=["grad", "tech_eng", "regtech"],
        default="tech_eng",
        help="CV framing variant (default: tech_eng). grad = graduate programs; "
        "tech_eng = generic engineering; regtech = compliance/RegTech roles.",
    )
    run_parser.set_defaults(func=run_flow)

    # search
    search_parser = subparsers.add_parser("search", help="Search for jobs (API-based, suitable for cron)")
    search_parser.set_defaults(func=search_jobs)

    # tailor — per-job manual workflow (one job + one variant -> PDFs)
    tailor_parser = subparsers.add_parser(
        "tailor",
        help="Tailor CV + cover letter for ONE job (from pipeline_jobs.json) and render PDFs",
    )
    tailor_group = tailor_parser.add_mutually_exclusive_group(required=True)
    tailor_group.add_argument("--job-id", type=str, help="Job ID from pipeline_jobs.json")
    tailor_group.add_argument(
        "--job-index", type=int, help="0-based index in pipeline_jobs.json"
    )
    tailor_parser.add_argument(
        "--variant",
        choices=["grad", "tech_eng", "regtech"],
        default="tech_eng",
        help="CV framing variant (default: tech_eng)",
    )
    tailor_parser.add_argument(
        "--no-eval", action="store_true",
        help="Skip the post-render ATS + recruiter-scan evaluation (faster; PDFs only)",
    )
    tailor_parser.set_defaults(func=tailor_one_job)

    # discover — Tier-1 (direct ATS) + Tier-2 (opencli LinkedIn) discovery
    discover_parser = subparsers.add_parser(
        "discover",
        help="Discover new Dublin jobs: Tier-1 (ATS poll) + Tier-2 (opencli LinkedIn)",
    )
    discover_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be discovered but don't write to pipeline_jobs.json",
    )
    discover_parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Dry-run output format (default: text)",
    )
    discover_parser.add_argument(
        "--skip-opencli", action="store_true",
        help="Skip Tier-2 opencli LinkedIn search (Tier-1 only)",
    )
    discover_parser.add_argument(
        "--queries", type=str, default="",
        help="Override LinkedIn queries (comma-separated). Default: target_roles + ' Dublin'",
    )
    discover_parser.add_argument(
        "--limit", type=int, default=25,
        help="LinkedIn results per query (default: 25)",
    )
    discover_parser.add_argument(
        "--date-posted", choices=["any", "month", "week", "24h"], default="week",
        help="LinkedIn freshness filter (default: week)",
    )
    discover_parser.add_argument(
        "--budget", type=int, default=20,
        help="Max opencli LinkedIn calls per day (default: 20)",
    )
    discover_parser.set_defaults(func=discover)

    # digest — Phase 1 Telegram push: top eng jobs → phone, one card per job
    digest_parser = subparsers.add_parser(
        "digest",
        help="Push today's top eng-flavored jobs to Telegram (one card per job)",
    )
    digest_parser.add_argument(
        "--limit", type=int, default=10,
        help="Max cards to send (default: 10)",
    )
    digest_parser.add_argument(
        "--max-age-days", type=int, default=7,
        help="Only send jobs posted within this many days (default: 7)",
    )
    digest_parser.add_argument(
        "--dry-run", action="store_true",
        help="Print formatted cards to stdout instead of sending to Telegram",
    )
    digest_parser.add_argument(
        "--reset", action="store_true",
        help="Clear data/digested.json before running (re-eligible all jobs)",
    )
    digest_parser.add_argument(
        "--send", action="store_true",
        help="With --reset: also send after clearing (default: clear-and-exit)",
    )
    digest_parser.set_defaults(func=digest)

    # bot
    bot_parser = subparsers.add_parser(
        "bot",
        help="Telegram bot daemon (handles Save/Skip taps on digest cards)",
    )
    bot_sub = bot_parser.add_subparsers(dest="bot_command", help="Bot commands")
    bot_run_parser = bot_sub.add_parser("run", help="Start the long-polling daemon")
    bot_run_parser.set_defaults(func=bot_run)

    # referrals
    referrals_parser = subparsers.add_parser(
        "referrals",
        help="Find 1st-degree LinkedIn connections at a company (referral discovery)",
    )
    referrals_parser.add_argument(
        "company",
        nargs="*",
        help="Company name to find referrers at; omit to list top companies by connection count",
    )
    referrals_parser.add_argument(
        "--targets",
        action="store_true",
        help="Cross-reference connections against target_companies.json (warm referral paths)",
    )
    referrals_parser.set_defaults(func=referrals_cmd)

    # gaps
    gaps_parser = subparsers.add_parser(
        "gaps",
        help="Aggregate ATS keyword gaps across data/work/*.json (master_cv audit input)",
    )
    gaps_parser.add_argument("--top", type=int, default=20, help="Show top N gaps (default 20)")
    gaps_parser.add_argument(
        "--recompute",
        action="store_true",
        help="Backfill evaluation.ats_score for stale work files first (calls Claude)",
    )
    gaps_parser.add_argument(
        "--force",
        action="store_true",
        help="With --recompute, recompute even files that already have ats_score",
    )
    gaps_parser.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format",
    )
    gaps_parser.set_defaults(func=gaps_scan)

    # init-profile
    profile_parser = subparsers.add_parser("init-profile", help="Initialize profile file")
    profile_parser.set_defaults(func=init_profile)

    # story
    story_parser = subparsers.add_parser("story", help="Manage your story bank")
    story_sub = story_parser.add_subparsers(dest="story_command", help="Story commands")

    # story add
    add_parser = story_sub.add_parser("add", help="Add a new story")
    add_parser.add_argument("--quick", type=str, help="Quick add: LLM structures a rough note")
    add_parser.set_defaults(func=story_add)

    # story list
    list_parser = story_sub.add_parser("list", help="List stories")
    list_parser.add_argument("--tag", type=str, help="Filter by tag")
    list_parser.add_argument("--skill", type=str, help="Filter by skill")
    list_parser.set_defaults(func=story_list)

    # story import
    import_parser = story_sub.add_parser("import", help="Import stories from text")
    import_parser.set_defaults(func=story_import)

    # story migrate
    migrate_parser = story_sub.add_parser("migrate", help="Migrate hardcoded stories")
    migrate_parser.set_defaults(func=story_migrate)

    # story edit
    edit_parser = story_sub.add_parser("edit", help="Edit a story in $EDITOR")
    edit_parser.add_argument("story_id", help="Story ID to edit")
    edit_parser.set_defaults(func=story_edit)

    # story delete
    del_parser = story_sub.add_parser("delete", help="Delete a story")
    del_parser.add_argument("story_id", help="Story ID to delete")
    del_parser.set_defaults(func=story_delete)

    # story refine
    refine_parser = story_sub.add_parser("refine", help="LLM-assisted story correction")
    refine_parser.add_argument("story_id", help="Story ID to refine")
    refine_parser.add_argument("correction", help="What to change (natural language)")
    refine_parser.set_defaults(func=story_refine)

    # status
    status_parser = subparsers.add_parser("status", help="Show application status")
    status_parser.set_defaults(func=app_status)

    # update
    update_parser = subparsers.add_parser("update", help="Update application status")
    update_parser.add_argument("job_id", help="Job ID to update")
    update_parser.add_argument("--status", required=True, help="New status")
    update_parser.add_argument("--notes", type=str, help="Optional notes")
    update_parser.set_defaults(func=app_update)

    # feedback
    feedback_parser = subparsers.add_parser("feedback", help="Record company response")
    feedback_parser.add_argument("job_id", help="Job ID")
    feedback_parser.add_argument(
        "--response", required=True,
        choices=["interview", "rejection", "ghosted", "offer", "no_response"],
        help="Response type",
    )
    feedback_parser.add_argument("--notes", type=str, help="Details about the response")
    feedback_parser.set_defaults(func=app_feedback)

    # inbox-sync — Phase 4: multi-account Gmail → applications.json auto-update
    inbox_parser = subparsers.add_parser(
        "inbox-sync",
        help="Read Gmail accounts, classify application emails, update applications.json + push Telegram",
    )
    inbox_parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + classify but don't write applications.json or push Telegram",
    )
    inbox_parser.add_argument(
        "--account", type=str, default=None,
        help="Limit to one email account (default: all accounts in inbox_accounts.json)",
    )
    inbox_parser.add_argument(
        "--query", type=str, default=None,
        help="Override Gmail search query (default: noreply/careers senders, newer_than:30d)",
    )
    inbox_parser.add_argument(
        "--no-telegram", action="store_true",
        help="Skip Telegram push even on status-change events",
    )
    inbox_parser.add_argument(
        "--non-interactive", action="store_true",
        help="Don't open a browser if OAuth refresh fails; push Telegram alert "
             "and exit non-zero instead. Use for launchd/cron runs.",
    )
    inbox_parser.set_defaults(func=inbox_sync)

    # cleanup — delete output folders for skipped / rejected jobs (keeps metadata)
    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="Delete CV/cover-letter output folders for skipped/rejected jobs "
             "(job metadata is kept for stats)",
    )
    cleanup_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show which folders would be deleted and the space freed, without deleting",
    )
    cleanup_parser.set_defaults(func=cleanup)

    args = parser.parse_args()
    if not args.command:
        # Default to run for backwards compatibility
        args.func = run_flow
        args.scheduled = False
        args.func(args)
        return

    if args.command == "story" and not getattr(args, "story_command", None):
        story_parser.print_help()
        return

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
