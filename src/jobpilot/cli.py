from __future__ import annotations

import argparse
import json
import os
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

    initial_state = {"search_query": ""}
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


def main() -> None:
    parser = argparse.ArgumentParser(description="JobPilot CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    run_parser = subparsers.add_parser("run", help="Run the job search pipeline")
    run_parser.add_argument("--scheduled", action="store_true", help="Scheduled mode (no interactive review)")
    run_parser.set_defaults(func=run_flow)

    # search
    search_parser = subparsers.add_parser("search", help="Search for jobs (API-based, suitable for cron)")
    search_parser.set_defaults(func=search_jobs)

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
