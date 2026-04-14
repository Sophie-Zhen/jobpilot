from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

from jobpilot.stories import Story


def _call_claude(prompt: str, timeout: int = 120, tools: list[str] | None = None) -> str:
    """Call Claude Code CLI and return the text response."""
    try:
        cmd = ["claude", "-p", "--output-format", "json"]
        if tools:
            cmd.extend(["--allowedTools", ",".join(tools)])
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "claude CLI not found. Install Claude Code: https://claude.ai/code"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Claude Code timed out after {timeout} seconds.")

    if result.returncode != 0:
        raise RuntimeError(f"Claude Code failed: {result.stderr[:500]}")

    try:
        envelope = json.loads(result.stdout)
        text = (envelope.get("result") or "").strip()
    except json.JSONDecodeError:
        # Fall back to raw stdout if not JSON
        text = result.stdout.strip()

    if not text:
        raise RuntimeError("Claude Code returned an empty response.")
    return text


def _parse_json_response(text: str) -> Any:
    """Extract JSON from Claude's response, handling markdown fences and preamble."""
    text = text.strip()
    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find the first JSON array or object in the text
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = text.find(start_char)
        if start == -1:
            continue
        # Find matching closing bracket, scanning from the end
        end = text.rfind(end_char)
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


def structure_story(raw_text: str) -> Story:
    prompt = (
        "Convert the following rough note into a structured professional story.\n"
        "Return a JSON object with these fields:\n"
        "- title: short descriptive title (max 10 words)\n"
        "- situation: the context or problem (1-2 sentences)\n"
        "- action: what was done (1-2 sentences)\n"
        "- result: the outcome with quantified impact if possible (1-2 sentences)\n"
        "- tags: list of relevant topic tags (lowercase)\n"
        "- skills: list of technical skills demonstrated (lowercase)\n\n"
        f"Raw note:\n{raw_text}\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    try:
        response = _call_claude(prompt)
        data = _parse_json_response(response)
        return Story(
            title=data.get("title", raw_text[:80]),
            situation=data.get("situation", ""),
            action=data.get("action", ""),
            result=data.get("result", ""),
            tags=data.get("tags", []),
            skills=data.get("skills", []),
            source="quick",
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to structure story: {exc}") from exc


def import_stories(raw_text: str) -> list[Story]:
    prompt = (
        "Extract individual professional stories from the following text.\n"
        "Each story should be a separate achievement, project, or experience.\n"
        "Return a JSON array of objects, each with:\n"
        "- title, situation, action, result, tags, skills\n\n"
        f"Text:\n{raw_text}\n\n"
        "Return ONLY a valid JSON array, no markdown fences."
    )
    try:
        response = _call_claude(prompt)
        data = _parse_json_response(response)
        stories = []
        for item in data:
            stories.append(
                Story(
                    title=item.get("title", "Untitled"),
                    situation=item.get("situation", ""),
                    action=item.get("action", ""),
                    result=item.get("result", ""),
                    tags=item.get("tags", []),
                    skills=item.get("skills", []),
                    source="import",
                )
            )
        return stories
    except Exception as exc:
        raise RuntimeError(f"Failed to import stories: {exc}") from exc


def refine_story(story: Story, correction: str) -> Story:
    story_json = json.dumps(story.model_dump(), indent=2)
    prompt = (
        "You have an existing professional story. Apply the user's correction.\n"
        "Return the updated story as a JSON object with the same fields.\n\n"
        f"Current story:\n{story_json}\n\n"
        f"Correction: {correction}\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    try:
        response = _call_claude(prompt)
        data = _parse_json_response(response)
        refined = Story(
            id=story.id,
            title=data.get("title", story.title),
            situation=data.get("situation", story.situation),
            action=data.get("action", story.action),
            result=data.get("result", story.result),
            tags=data.get("tags", story.tags),
            skills=data.get("skills", story.skills),
            date_added=story.date_added,
            date_occurred=data.get("date_occurred", story.date_occurred),
            source=story.source,
        )
        return refined
    except Exception as exc:
        raise RuntimeError(f"Failed to refine story: {exc}") from exc


def migrate_legacy_stories(raw_stories: list[dict[str, Any]]) -> list[Story]:
    stories_json = json.dumps(raw_stories, indent=2)
    prompt = (
        "Convert each legacy story into STAR format.\n"
        "Each story has title, content, and tags.\n"
        "For each, return: title, situation, action, result, tags, skills.\n"
        "Return a JSON array.\n\n"
        f"Stories:\n{stories_json}\n\n"
        "Return ONLY a valid JSON array, no markdown fences."
    )
    try:
        response = _call_claude(prompt)
        data = _parse_json_response(response)
        stories = []
        for item in data:
            stories.append(
                Story(
                    title=item.get("title", "Untitled"),
                    situation=item.get("situation", ""),
                    action=item.get("action", ""),
                    result=item.get("result", ""),
                    tags=item.get("tags", []),
                    skills=item.get("skills", []),
                    source="migration",
                )
            )
        return stories
    except Exception as exc:
        raise RuntimeError(f"Failed to migrate stories: {exc}") from exc


def _load_master_cv() -> dict[str, Any]:
    master_path = Path(__file__).resolve().parent.parent.parent / "data" / "master_cv.json"
    if not master_path.exists():
        raise RuntimeError(f"Master CV not found at {master_path}. Create data/master_cv.json first.")
    return json.loads(master_path.read_text(encoding="utf-8"))


def _apply_adjustments(
    master: dict[str, Any],
    adjustments: dict[str, Any],
    current_cv: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply adjustment diffs to master_cv, with optional fallback to current_cv."""
    def _get(key: str, default: Any = None) -> Any:
        val = adjustments.get(key)
        if val is not None:
            return val
        if current_cv is not None:
            return current_cv.get(key, default)
        return default

    result = {
        **master["contact"],
        "summary": _get("summary", ""),
    }

    # Education
    include_fudan = _get("include_fudan", False)
    education = []
    for edu in master["education"]:
        if edu.get("optional") and not include_fudan:
            continue
        education.append(edu)
    result["education"] = education

    # Experience — select bullets
    bullet_indices = _get("experience_bullet_indices", {})
    experience = []
    for exp in master["experience"]:
        exp_copy = copy.deepcopy(exp)
        indices = bullet_indices.get(exp["id"])
        if indices is not None:
            exp_copy["bullets"] = [exp["bullets"][i] for i in indices if i < len(exp["bullets"])]
        elif current_cv:
            # Keep current selection if no new indices provided
            for cur_exp in current_cv.get("experience", []):
                if cur_exp.get("id") == exp["id"]:
                    exp_copy["bullets"] = cur_exp["bullets"]
                    break
            else:
                exp_copy["bullets"] = exp["bullets"][:3]
        else:
            exp_copy["bullets"] = exp["bullets"][:3]
        experience.append(exp_copy)
    result["experience"] = experience

    # Projects — select which to include
    project_ids = _get("project_ids", [p["id"] for p in master["projects"][:3]])
    projects = [p for p in master["projects"] if p["id"] in project_ids]
    result["projects"] = projects

    # Skills — categorized
    skills_adj = _get("skills", None)
    if isinstance(skills_adj, dict) and skills_adj:
        result["skills"] = skills_adj
    else:
        result["skills"] = {
            "Languages": master["skills"]["languages"],
            "ML & AI": master["skills"]["ml_ai"],
            "Tools & Frameworks": master["skills"]["tools"],
            "Other": master["skills"]["other"],
        }

    # Awards
    if _get("include_awards", True):
        result["awards"] = master["awards"]
    else:
        result["awards"] = []

    return result


def _reverse_bullet_indices(cv_bullets: list[str], master_bullets: list[str]) -> list[int]:
    """Find which master bullet indices are selected in cv_bullets."""
    return [i for i, b in enumerate(master_bullets) if b in cv_bullets]


def _compact_cv_summary(cv_data: dict[str, Any], master: dict[str, Any]) -> str:
    """Compact representation of current CV selections (~15 lines vs ~150 lines of JSON)."""
    lines = [f"Summary: {cv_data.get('summary', '')[:200]}"]

    # Education
    edu_names = [e.get("institution", "")[:30] for e in cv_data.get("education", [])]
    lines.append(f"Education: {', '.join(edu_names)}")

    # Experience bullet selections
    lines.append("Experience selections:")
    for exp in cv_data.get("experience", []):
        exp_id = exp.get("id", "?")
        master_exp = next((e for e in master["experience"] if e["id"] == exp_id), None)
        if master_exp:
            indices = _reverse_bullet_indices(exp.get("bullets", []), master_exp["bullets"])
            lines.append(f"  {exp_id}: bullets {indices} of {len(master_exp['bullets'])}")
        else:
            lines.append(f"  {exp_id}: {len(exp.get('bullets', []))} bullets")

    # Projects
    proj_ids = [p.get("id", "?") for p in cv_data.get("projects", [])]
    lines.append(f"Projects: {proj_ids}")

    # Skills
    skills = cv_data.get("skills", {})
    if isinstance(skills, dict):
        for cat, items in skills.items():
            lines.append(f"  {cat}: {', '.join(items[:5])}{'...' if len(items) > 5 else ''}")
    else:
        lines.append(f"Skills: {', '.join(skills[:10])}")

    lines.append(f"Awards: {'included' if cv_data.get('awards') else 'excluded'}")
    return "\n".join(lines)


def tailor_cv(
    job: dict[str, Any],
    stories: list[Story],
    profile: dict[str, Any],
    role_level: str | None = None,
) -> dict[str, Any]:
    """Generate a tailored CV by adjusting the master CV data for a specific job."""
    master = _load_master_cv()

    if role_level is None:
        role_level = classify_role_level(job)

    level_instructions = ROLE_LEVEL_INSTRUCTIONS.get(role_level, ROLE_LEVEL_INSTRUCTIONS["mid"])
    job_description = job.get("full_description", job.get("description", ""))

    exp_summary = "\n".join(
        f"  [{e['id']}] {e['title']} at {e['company']} ({e['dates']}): {len(e['bullets'])} bullets"
        for e in master["experience"]
    )
    proj_summary = "\n".join(
        f"  [{p['id']}] {p['title']} ({p['tech']})"
        for p in master["projects"]
    )
    all_skills = master["skills"]["languages"] + master["skills"]["ml_ai"] + master["skills"]["tools"] + master["skills"]["other"]

    prompt = (
        f"{level_instructions}\n\n"
        "You are tailoring a CV for a specific job. The candidate's full CV data is already prepared.\n"
        "You only need to provide ADJUSTMENTS — do not rewrite the whole CV.\n"
        "Follow the role-level guidance above carefully.\n\n"
        f"JOB:\nTitle: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
        f"Description: {job_description[:2000]}\n\n"
        f"AVAILABLE EXPERIENCE:\n{exp_summary}\n\n"
        f"AVAILABLE PROJECTS:\n{proj_summary}\n\n"
        f"AVAILABLE SKILLS BY CATEGORY:\n"
        f"  Languages: {', '.join(master['skills']['languages'])}\n"
        f"  ML & AI: {', '.join(master['skills']['ml_ai'])}\n"
        f"  Tools & Frameworks: {', '.join(master['skills']['tools'])}\n"
        f"  Other: {', '.join(master['skills']['other'])}\n\n"
        "Return a JSON object with these adjustments:\n"
        "- summary: professional summary tailored to THIS role (2-3 sentences). "
        "Follow the role-level guidance for framing.\n"
        "- include_fudan: true/false — include the Fudan MA (useful for policy/international roles)\n"
        "- experience_bullet_indices: for each experience ID, an array of bullet indices (0-based) "
        "to INCLUDE. Follow role-level guidance for how many per role. "
        "Format: {\"huawei\": [0, 1], \"walkers\": [0, 2, 3], \"tax_bureau\": [0, 2, 3]}\n"
        "- project_ids: array of project IDs to include. Follow role-level guidance for count. "
        f"Choose from: {[p['id'] for p in master['projects']]}\n"
        "- skills: object with categories, each containing skills ordered by relevance to THIS job. "
        "You may add 1-2 job-specific skills if the candidate clearly has them. "
        "Format: {\"Languages\": [\"Python\", ...], \"ML & AI\": [...], \"Tools & Frameworks\": [...], \"Other\": [...]}\n"
        "- include_awards: true/false — include awards section\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )

    try:
        response = _call_claude(prompt, timeout=120)
        adjustments = _parse_json_response(response)
    except Exception as exc:
        raise RuntimeError(f"CV tailoring failed: {exc}") from exc

    return _apply_adjustments(master, adjustments)


def revise_cv(
    cv_data: dict[str, Any],
    cover_letter: str,
    job: dict[str, Any],
    feedback: str,
) -> tuple[dict[str, Any], str]:
    """Revise a tailored CV and cover letter based on user feedback."""
    cv_json = json.dumps(cv_data, indent=2, ensure_ascii=False)
    job_description = job.get("full_description", job.get("description", ""))

    prompt = (
        "You have a tailored CV and cover letter. The user wants changes.\n"
        "Apply the feedback precisely. Do NOT change anything the user didn't ask about.\n"
        "Return a JSON object with two keys:\n"
        '- "cv": the full updated CV object (same structure as input)\n'
        '- "cover_letter": the updated cover letter text\n\n'
        f"JOB: {job.get('title', '')} at {job.get('company', '')}\n"
        f"Description: {job_description[:1000]}\n\n"
        f"CURRENT CV:\n{cv_json}\n\n"
        f"CURRENT COVER LETTER:\n{cover_letter[:1500]}\n\n"
        f"USER FEEDBACK: {feedback}\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    try:
        response = _call_claude(prompt, timeout=120)
        data = _parse_json_response(response)
        return data["cv"], data["cover_letter"]
    except Exception as exc:
        raise RuntimeError(f"CV revision failed: {exc}") from exc


def generate_cover_letter(
    job: dict[str, Any],
    stories: list[Story],
    profile: dict[str, Any],
    role_level: str | None = None,
) -> str:
    if role_level is None:
        role_level = classify_role_level(job)

    stories_text = "\n".join(
        f"- {s.title}{' [' + s.experience_id + ']' if s.experience_id else ''}: {s.result}"
        for s in stories[:5]
    )
    job_description = job.get("full_description", job.get("description", ""))

    # Load master cover letter template for selling points and closing style
    cl_template_path = Path(__file__).resolve().parent.parent.parent / "data" / "master_cover_letter.json"
    selling_points = ""
    closing_style = ""
    if cl_template_path.exists():
        try:
            cl_data = json.loads(cl_template_path.read_text(encoding="utf-8"))
            points = cl_data.get("key_selling_points", [])
            if points:
                selling_points = "\nKey selling points to weave in (use the relevant ones):\n" + "\n".join(f"- {p}" for p in points) + "\n"
            closing_style = cl_data.get("closing", "")
        except Exception:
            pass

    level_note = {
        "graduate": "The candidate is a career changer applying for a graduate role. Emphasize learning, passion, and transferable skills. Don't overstate experience.",
        "junior": "The candidate is a career changer. Balance enthusiasm with professional maturity.",
        "mid": "Blend technical depth with professional experience.",
        "senior": "Lead with leadership, scale of systems, and cross-functional impact.",
    }.get(role_level, "")

    prompt = (
        "Write a professional cover letter for the following job application.\n"
        "Use specific examples from the candidate's stories to demonstrate fit.\n"
        "Keep it concise: 3-4 paragraphs, under 400 words.\n"
        f"{level_note}\n"
        "NEVER exaggerate or invent experience.\n\n"
        f"JOB: {job.get('title', '')} at {job.get('company', '')}\n"
        f"Description: {job_description[:800]}\n\n"
        f"CANDIDATE: {profile.get('name', '')}, {profile.get('location', '')}\n"
        f"Key stories:\n{stories_text}\n"
        f"{selling_points}\n"
        "Write the cover letter directly. Start with 'Dear Hiring Manager,' or similar.\n"
        f"{'End with a closing similar to: ' + closing_style if closing_style else ''}\n"
        "Do NOT include any preamble like 'Here is the cover letter:' or '---'.\n"
        "No JSON wrapping, no markdown fences."
    )
    try:
        return _call_claude(prompt)
    except Exception as exc:
        raise RuntimeError(f"Cover letter generation failed: {exc}") from exc


def fetch_full_jd(job_url: str) -> str:
    """Fetch the full job description from a URL using Claude Code WebFetch."""
    if not job_url:
        return ""
    prompt = (
        f"Fetch the page at this URL and extract the full job description:\n"
        f"{job_url}\n\n"
        "Extract ONLY the job description content: responsibilities, requirements, "
        "qualifications, and other relevant details. Do NOT include navigation, "
        "headers, footers, or other page chrome.\n"
        "Return plain text, not JSON."
    )
    try:
        return _call_claude(prompt, timeout=60, tools=["WebFetch"])
    except Exception:
        return ""


def classify_role_level(job: dict[str, Any]) -> str:
    """Classify a job as graduate, junior, mid, or senior."""
    description = job.get("full_description", job.get("description", ""))
    title = job.get("title", "")

    prompt = (
        "Classify this job by seniority level.\n\n"
        f"Title: {title}\n"
        f"Description: {description[:1500]}\n\n"
        'Return ONLY a JSON object: {"level": "graduate|junior|mid|senior"}\n'
        "No markdown fences."
    )
    try:
        response = _call_claude(prompt, timeout=30)
        data = _parse_json_response(response)
        level = data.get("level", "mid").lower()
        if level in ("graduate", "junior", "mid", "senior"):
            return level
        return "mid"
    except Exception:
        title_lower = title.lower()
        if any(w in title_lower for w in ("graduate", "grad", "entry", "intern")):
            return "graduate"
        if "junior" in title_lower or "jr" in title_lower:
            return "junior"
        if any(w in title_lower for w in ("senior", "sr", "lead", "principal", "staff")):
            return "senior"
        return "mid"


ROLE_LEVEL_INSTRUCTIONS: dict[str, str] = {
    "graduate": (
        "ROLE LEVEL: Graduate/Entry-level. The candidate is a career changer "
        "with 13 years of non-IT professional experience.\n"
        "- Do NOT lead with '13+ years of experience' — it looks overqualified.\n"
        "- Lead the summary with: MSc Computer Science (NLP) from DCU, First-Class Honours (top 5%), "
        "genuine passion for AI demonstrated by projects and research.\n"
        "- Frame career change as a STRENGTH: analytical rigour, real-world problem solving, "
        "stakeholder communication, and the courage to start fresh.\n"
        "- Emphasize: recent education, hands-on projects, learning speed, Huawei research.\n"
        "- Tax bureau: pick only 1 bullet showing transferable skills (automation or data analysis).\n"
        "- Include more projects (3-4) to demonstrate technical depth.\n"
        "- Skills: technical skills first, de-emphasize 'Financial Auditing'.\n"
        "- NEVER exaggerate or invent experience."
    ),
    "junior": (
        "ROLE LEVEL: Junior. The candidate is a career changer.\n"
        "- Balance recent MSc (top 5%) with transferable professional experience.\n"
        "- Don't hide the 13 years but frame as 'professional background' not 'IT experience'.\n"
        "- Emphasize hands-on projects, Huawei research, and neuro-symbolic pipeline.\n"
        "- Tax bureau: pick 2 bullets showing relevant transferable skills.\n"
        "- Include 2-3 projects.\n"
        "- NEVER exaggerate or invent experience."
    ),
    "mid": (
        "ROLE LEVEL: Mid-level.\n"
        "- Blend the MSc (NLP, top 5%) with depth of professional experience.\n"
        "- 13 years is an asset — emphasize analytical depth, scale of systems, "
        "combination of domain + technical expertise.\n"
        "- Highlight Huawei research and Walkers practicum as recent relevant experience.\n"
        "- Tax bureau: pick 2-3 bullets showing scale, data work, automation.\n"
        "- Include 2-3 projects.\n"
        "- NEVER exaggerate or invent experience."
    ),
    "senior": (
        "ROLE LEVEL: Senior. The candidate has deep professional experience "
        "but is relatively new to IT/AI specifically.\n"
        "- Lead with 13+ years professional experience combined with recent MSc.\n"
        "- Emphasize: leadership, national-scale systems (Golden Tax Project), "
        "cross-functional stakeholder management, technical writing.\n"
        "- Frame tax bureau as 'government technology and data infrastructure'.\n"
        "- Huawei and Walkers show cutting-edge AI work.\n"
        "- Include 2-3 projects showing end-to-end delivery.\n"
        "- Highlight 'Workflow Automation' and 'Financial Auditing' skills.\n"
        "- Be honest about career transition — position as unique perspective.\n"
        "- NEVER exaggerate or invent experience."
    ),
}


def evaluate_cv(
    cv_data: dict[str, Any],
    job: dict[str, Any],
    cover_letter: str,
) -> dict[str, Any]:
    """Independent evaluation of a tailored CV against the job description."""
    job_description = job.get("full_description", job.get("description", ""))

    cv_parts = [f"Summary: {cv_data.get('summary', '')}"]
    for exp in cv_data.get("experience", []):
        cv_parts.append(f"\n{exp.get('title', '')} at {exp.get('company', '')} ({exp.get('dates', '')})")
        for bullet in exp.get("bullets", []):
            cv_parts.append(f"  - {bullet}")
    skills = cv_data.get("skills", {})
    if isinstance(skills, dict):
        for cat, items in skills.items():
            cv_parts.append(f"\n{cat}: {', '.join(items)}")
    else:
        cv_parts.append(f"\nSkills: {', '.join(skills)}")
    for proj in cv_data.get("projects", []):
        cv_parts.append(f"\nProject: {proj.get('title', '')} ({proj.get('tech', '')})")
        for bullet in proj.get("bullets", []):
            cv_parts.append(f"  - {bullet}")
    cv_text = "\n".join(cv_parts)

    prompt = (
        "You are an experienced technical recruiter and ATS reviewer.\n"
        "Evaluate this CV and cover letter against the job description.\n"
        "Be critical but fair. The candidate is a career changer (tax/government -> AI/ML).\n\n"
        f"JOB DESCRIPTION:\n{job_description[:2000]}\n\n"
        f"CV:\n{cv_text}\n\n"
        f"COVER LETTER:\n{cover_letter[:1500]}\n\n"
        "Return a JSON object with:\n"
        "- overall_score: 1-10\n"
        "- keyword_coverage: {matched: [...], missing_critical: [...], missing_nice_to_have: [...]}\n"
        "- experience_fit: 1-10 with one-sentence explanation\n"
        "- red_flags: list of strings a recruiter would question\n"
        "- ats_issues: list of formatting/keyword issues\n"
        "- strengths: list of what works well\n"
        "- suggestions: list of specific actionable improvements\n"
        "- would_shortlist: true/false with reasoning\n"
        "- gaps: an object analyzing missing skills/experience with two keys:\n"
        "    quick_fill: array of objects, each with:\n"
        "      - skill: name of the missing skill\n"
        "      - reason_missing: why the CV doesn't cover it\n"
        "      - how_to_fill: concrete instructions to acquire this skill in a few hours "
        "(specific tutorial, exercise, or mini-project the candidate can do quickly)\n"
        "      - suggested_bullet: a one-line CV bullet the candidate could add AFTER completing the exercise\n"
        "    hard_gaps: array of strings describing missing skills/experience that "
        "cannot be acquired quickly (e.g. '5+ years production ML experience'). "
        "Explain WHY each one is hard to fill.\n"
        "  Only mark something quick_fill if it can genuinely be learned in under 4 hours "
        "of focused work. Otherwise it's a hard_gap.\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    try:
        response = _call_claude(prompt, timeout=120)
        return _parse_json_response(response)
    except Exception as exc:
        raise RuntimeError(f"CV evaluation failed: {exc}") from exc


def evaluate_and_suggest(
    cv_data: dict[str, Any],
    job: dict[str, Any],
    cover_letter: str,
    stories: list[Story],
    master: dict[str, Any],
    role_level: str | None = None,
) -> dict[str, Any]:
    """Evaluate a CV AND suggest adjustment diffs in a single LLM call.

    Returns {evaluation: {...}, adjustments: {...} or null, cover_letter: str or null}.
    Adjustments use the same schema as tailor_cv (bullet indices, project IDs, etc.)
    so they can be applied programmatically via _apply_adjustments.
    """
    job_description = job.get("full_description", job.get("description", ""))
    cv_summary = _compact_cv_summary(cv_data, master)

    # Available bullets with text (abbreviated)
    exp_options = []
    for exp in master["experience"]:
        exp_options.append(f"  [{exp['id']}] {exp['title']} at {exp['company']} ({exp['dates']})")
        for i, bullet in enumerate(exp["bullets"]):
            exp_options.append(f"    [{i}] {bullet[:100]}{'...' if len(bullet) > 100 else ''}")
    exp_text = "\n".join(exp_options)

    proj_options = "\n".join(
        f"  [{p['id']}] {p['title']} ({p['tech']})"
        for p in master["projects"]
    )

    stories_text = "\n".join(
        f"  - {s.title}{' [' + s.experience_id + ']' if s.experience_id else ''}: "
        f"{s.result[:80]} (skills: {', '.join(s.skills[:5])})"
        for s in stories
    )

    role_note = ""
    if role_level and role_level in ROLE_LEVEL_INSTRUCTIONS:
        role_note = ROLE_LEVEL_INSTRUCTIONS[role_level] + "\n\n"

    prompt = (
        "You are an experienced technical recruiter who also optimizes CVs.\n\n"
        "TASK: Evaluate this CV against the job. If it wouldn't be shortlisted, "
        "suggest ADJUSTMENT DIFFS to improve it.\n\n"
        "HONESTY RULES (override everything):\n"
        "- You may ONLY select bullets/projects/skills that exist in the AVAILABLE OPTIONS below.\n"
        "- NEVER invent experience, skills, or achievements.\n"
        "- If a critical keyword from the job is not available in any option, leave it missing "
        "and flag it in the gaps section.\n\n"
        f"{role_note}"
        f"JOB:\nTitle: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
        f"Description: {job_description[:2000]}\n\n"
        f"CURRENT CV SELECTIONS:\n{cv_summary}\n\n"
        f"AVAILABLE EXPERIENCE (pick bullet indices from these):\n{exp_text}\n\n"
        f"AVAILABLE PROJECTS:\n{proj_options}\n\n"
        "AVAILABLE SKILLS BY CATEGORY:\n"
        f"  Languages: {', '.join(master['skills']['languages'])}\n"
        f"  ML & AI: {', '.join(master['skills']['ml_ai'])}\n"
        f"  Tools & Frameworks: {', '.join(master['skills']['tools'])}\n"
        f"  Other: {', '.join(master['skills']['other'])}\n\n"
        f"AVAILABLE STORIES (for cover letter context):\n{stories_text}\n\n"
        f"CURRENT COVER LETTER:\n{cover_letter[:1500]}\n\n"
        "Return a JSON object with three keys:\n"
        '1. "evaluation": {\n'
        "   overall_score (1-10), keyword_coverage {matched, missing_critical, missing_nice_to_have},\n"
        "   experience_fit (1-10 with explanation), red_flags [], ats_issues [],\n"
        "   strengths [], suggestions [], would_shortlist (true/false with reasoning),\n"
        "   gaps: {quick_fill: [{skill, reason_missing, how_to_fill, suggested_bullet}], "
        "hard_gaps: [string]}\n"
        "}\n"
        '2. "adjustments": object with ONLY fields that need changing, or null if CV is already optimal.\n'
        "   Fields: summary, include_fudan, experience_bullet_indices, project_ids, skills, include_awards.\n"
        "   Same format as the CURRENT CV SELECTIONS — use bullet indices, project IDs, etc.\n"
        '3. "cover_letter": revised cover letter text, or null if adequate.\n\n'
        "Return ONLY valid JSON, no markdown fences."
    )
    try:
        response = _call_claude(prompt, timeout=180)
        return _parse_json_response(response)
    except Exception as exc:
        raise RuntimeError(f"Evaluate and suggest failed: {exc}") from exc


def auto_tailor_loop(
    job: dict[str, Any],
    stories: list[Story],
    profile: dict[str, Any],
    role_level: str | None = None,
    max_iterations: int = 3,
    progress_cb: Any = None,
) -> dict[str, Any]:
    """Tailor → evaluate+suggest loop until would_shortlist=True or max_iterations.

    Each iteration is ONE LLM call (evaluate_and_suggest) that returns both the
    evaluation and adjustment diffs. Adjustments are applied programmatically to
    master_cv.json, so fabrication is structurally impossible.
    """
    master = _load_master_cv()

    def _progress(msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    _progress("Initial tailoring...")
    cv = tailor_cv(job, stories, profile, role_level=role_level)
    if role_level:
        cv["role_level"] = role_level

    _progress("Generating cover letter...")
    cl = generate_cover_letter(job, stories, profile, role_level=role_level)

    iterations: list[dict[str, Any]] = []
    evaluation: dict[str, Any] = {}

    for i in range(max_iterations):
        _progress(f"Iteration {i + 1}: evaluating and suggesting improvements...")
        result = evaluate_and_suggest(cv, job, cl, stories, master, role_level)
        evaluation = result.get("evaluation", {})
        iterations.append({
            "iter": i + 1,
            "score": evaluation.get("overall_score", 0),
            "shortlist": bool(evaluation.get("would_shortlist")),
        })

        if evaluation.get("would_shortlist"):
            _progress(f"Shortlisted at iteration {i + 1} (score: {evaluation.get('overall_score', '?')}/10)")
            break

        if i < max_iterations - 1:
            _progress(f"Iteration {i + 1}: score {evaluation.get('overall_score', '?')}/10, applying adjustments...")
            adjustments = result.get("adjustments")
            if adjustments:
                cv = _apply_adjustments(master, adjustments, current_cv=cv)
                if role_level:
                    cv["role_level"] = role_level
            if result.get("cover_letter"):
                cl = result["cover_letter"]
        else:
            _progress(f"Max iterations reached (score: {evaluation.get('overall_score', '?')}/10)")

    return {
        "cv": cv,
        "cover_letter": cl,
        "evaluation": evaluation,
        "iterations": iterations,
    }


def search_jobs_web(
    queries: list[str],
    location: str = "Dublin, Ireland",
    days: int = 7,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Search for jobs using Claude Code web search."""
    from jobpilot.job_sources import _extract_skills_from_text
    from jobpilot.profile import load_profile
    from jobpilot.config import load_settings

    profile = load_profile(load_settings())
    profile_candidates = profile.get("skills", []) + profile.get("preferred_keywords", [])

    query_str = " OR ".join(f'"{q}"' for q in queries[:3])
    prompt = (
        f"Search LinkedIn for: {query_str} in {location}, posted last {days} days.\n"
        f"Use this URL: https://ie.linkedin.com/jobs/search?keywords={queries[0].replace(' ', '%20')}&location={location.replace(' ', '%20')}&f_TPR=r604800\n"
        f"Return up to {limit} jobs as a JSON array: [{{title, company, location, url, source, posted, description}}]\n"
        "For description, include a brief summary of key requirements and skills mentioned.\n"
        "Return ONLY valid JSON, no markdown."
    )
    try:
        response = _call_claude(
            prompt,
            timeout=300,
            tools=["WebSearch", "WebFetch"],
        )
        data = _parse_json_response(response)
        # Normalize to match the existing job format
        jobs = []
        for item in data:
            title = item.get("title", "")
            description = item.get("description", "")
            full_text = f"{title} {description}"
            jobs.append({
                "id": f"web_{hash(item.get('url', item.get('title', ''))) & 0xFFFFFFFF:08x}",
                "title": title,
                "company": item.get("company", ""),
                "location": item.get("location", location),
                "description": description,
                "url": item.get("url", ""),
                "source": item.get("source", "web"),
                "posted": item.get("posted", ""),
                "skills": _extract_skills_from_text(full_text, profile_candidates),
            })
        return jobs
    except Exception as exc:
        raise RuntimeError(f"Web job search failed: {exc}") from exc
