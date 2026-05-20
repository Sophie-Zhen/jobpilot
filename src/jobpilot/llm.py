from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from jobpilot.stories import Story


def _call_claude(
    prompt: str,
    timeout: int = 120,
    tools: list[str] | None = None,
    model: str | None = None,
) -> str:
    """Call Claude Code CLI and return the text response.

    ``model`` accepts a CLI alias ('haiku', 'sonnet', 'opus') or a full ID
    like 'claude-haiku-4-5-20251001'. When None, inherits whatever Claude
    Code is configured for the user (typically Opus for this project).
    """
    try:
        cmd = ["claude", "-p", "--output-format", "json"]
        if model:
            cmd.extend(["--model", model])
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
    variant: str = "tech_eng",
) -> dict[str, Any]:
    """Generate a tailored CV by adjusting the master CV data for a specific job.

    variant selects the identity-framing lens applied to the prompt:
    - 'grad'     — graduate-program targets; lead with MSc + projects
    - 'tech_eng' — generic engineering targets; engineer-first (default)
    - 'regtech'  — RegTech/compliance/legal-AI targets; tax-domain + AI dual-anchor
    """
    if variant not in FRAMING_RULES_BY_VARIANT:
        raise ValueError(
            f"Unknown variant {variant!r}; expected one of {VALID_VARIANTS}"
        )

    master = _load_master_cv()
    framing_rules = FRAMING_RULES_BY_VARIANT[variant]

    # For grad variant, role_level is forced to 'graduate' regardless of JD classification.
    if variant == "grad":
        role_level = "graduate"
    elif role_level is None:
        role_level = classify_role_level(job)

    level_instructions = ROLE_LEVEL_INSTRUCTIONS.get(role_level, ROLE_LEVEL_INSTRUCTIONS["mid"])
    job_description = job.get("full_description", job.get("description", ""))

    # Regtech variant restores Financial Auditing to the visible skills list — it is
    # a positive signal for this audience and was stripped from master_cv defaults.
    prompt_skills_other = list(master["skills"]["other"])
    if variant == "regtech" and "Financial Auditing" not in prompt_skills_other:
        prompt_skills_other.append("Financial Auditing")

    exp_summary = "\n".join(
        f"  [{e['id']}] {e['title']} at {e['company']} ({e['dates']}): {len(e['bullets'])} bullets"
        for e in master["experience"]
    )
    proj_summary = "\n".join(
        f"  [{p['id']}] {p['title']} ({p['tech']})"
        for p in master["projects"]
    )

    prompt = (
        f"{framing_rules}\n"
        f"{level_instructions}\n\n"
        f"{TONE_RULES}\n"
        f"You are tailoring a CV for a specific job (variant: {variant}). The candidate's full CV data is already prepared.\n"
        "You only need to provide ADJUSTMENTS — do not rewrite the whole CV.\n"
        "Follow the FRAMING_RULES and role-level guidance above carefully.\n\n"
        f"JOB:\nTitle: {job.get('title', '')}\nCompany: {job.get('company', '')}\n"
        f"Description: {job_description[:2000]}\n\n"
        f"AVAILABLE EXPERIENCE:\n{exp_summary}\n\n"
        f"AVAILABLE PROJECTS:\n{proj_summary}\n\n"
        f"AVAILABLE SKILLS BY CATEGORY:\n"
        f"  Languages: {', '.join(master['skills']['languages'])}\n"
        f"  ML & AI: {', '.join(master['skills']['ml_ai'])}\n"
        f"  Tools & Frameworks: {', '.join(master['skills']['tools'])}\n"
        f"  Other: {', '.join(prompt_skills_other)}\n\n"
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
        response = _call_claude(prompt, timeout=600)
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
        f"{TONE_RULES}\n"
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
        response = _call_claude(prompt, timeout=600)
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

    # Load winning cover letter examples (from jobs that got interviews)
    winning_examples = ""
    winning_dir = Path(__file__).resolve().parent.parent.parent / "data" / "winning_cover_letters"
    if winning_dir.exists():
        examples = []
        for wf in sorted(winning_dir.glob("*.json"))[-2:]:
            try:
                wd = json.loads(wf.read_text(encoding="utf-8"))
                examples.append(
                    f"[Example from {wd.get('job_title', '')} at {wd.get('company', '')} "
                    f"— this got an interview]\n{wd['cover_letter'][:500]}"
                )
            except Exception:
                continue
        if examples:
            winning_examples = (
                "\nHere are cover letters that previously got interviews. "
                "Match their tone and approach:\n" + "\n---\n".join(examples) + "\n"
            )

    level_note = {
        "graduate": "The candidate is a career changer applying for a graduate role. Emphasize learning, passion, and transferable skills. Don't overstate experience.",
        "junior": "The candidate is a career changer. Balance enthusiasm with professional maturity.",
        "mid": "Blend technical depth with professional experience.",
        "senior": "Lead with leadership, scale of systems, and cross-functional impact.",
    }.get(role_level, "")

    prompt = (
        f"{TONE_RULES}\n"
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
        f"{winning_examples}"
        "Write the cover letter directly. Start with 'Dear Hiring Manager,' or similar.\n"
        f"{'End with a closing similar to: ' + closing_style if closing_style else ''}\n"
        "Do NOT include any preamble like 'Here is the cover letter:' or '---'.\n"
        "Do NOT include a sign-off line (Sincerely/Best regards/Yours/etc) or the candidate's name at the end — "
        "the document template adds the signature automatically. End on the final paragraph of body text.\n"
        "No JSON wrapping, no markdown fences."
    )
    try:
        return _strip_signature(_call_claude(prompt))
    except Exception as exc:
        raise RuntimeError(f"Cover letter generation failed: {exc}") from exc


_SIGNOFF_RE = re.compile(
    r"^(sincerely|best regards|kind regards|warm regards|regards|best|yours sincerely|"
    r"yours faithfully|yours truly|cordially|respectfully|thank you|thanks)\b[\s,.!-]*$",
    re.IGNORECASE,
)


def _strip_signature(text: str) -> str:
    """Strip any trailing sign-off + name lines from a cover-letter body.

    The LaTeX template hardcodes "Sincerely,\\\\<name>" at the end, so any sign-off
    or name the LLM emits would duplicate it. Walks backwards from the end and drops
    blank lines, sign-off phrases, and short name-like lines until it hits substantive
    body content.
    """
    if not text:
        return text
    lines = text.rstrip().split("\n")
    while lines:
        last = lines[-1].strip()
        if not last:
            lines.pop()
            continue
        if _SIGNOFF_RE.match(last):
            lines.pop()
            continue
        # Heuristic: short line (<=5 words, <=60 chars) with no terminal punctuation
        # is almost certainly a signature name, not a body sentence.
        words = last.split()
        if len(words) <= 5 and len(last) <= 60 and not last.endswith((".", "?", "!", ":")):
            lines.pop()
            continue
        break
    return "\n".join(lines).rstrip()


def summarize_jd(description: str, title: str = "", company: str = "") -> str:
    """Generate a 1-2 sentence summary of a job description."""
    if not description or len(description.strip()) < 50:
        return ""
    prompt = (
        "Summarize this job posting in 1-2 sentences. Focus on: what the role does, "
        "key technical requirements, and team/domain. Be specific, not generic.\n\n"
        f"Title: {title}\nCompany: {company}\n"
        f"Description:\n{description[:2000]}\n\n"
        "Return ONLY the summary text, no labels or prefixes."
    )
    try:
        return _call_claude(prompt, timeout=30).strip()
    except Exception:
        return ""


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
        # Haiku trial (2026-05-18): classify_role_level is a 4-way categorical
        # extraction over a short JD snippet — no deep reasoning needed.
        # If Haiku misjudges (e.g. labels Staff/Principal as "senior" instead
        # of separating, or trips on edge cases), revert to default Opus.
        response = _call_claude(prompt, timeout=30, model="haiku")
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


TONE_RULES = (
    "WRITING TONE — CRITICAL RULES:\n"
    "Write like a real person, not an AI. Recruiters flag AI-generated content.\n"
    "BANNED WORDS/PHRASES (never use these): leverage, utilize, spearhead, delve, "
    "cutting-edge, drive/driven, passionate, innovative, synergy, foster, "
    "elevate, landscape, multifaceted, cornerstone, paradigm, robust, "
    "transformative, holistic, seamless, pivotal, embark, underscores, "
    "harnessing, navigating, intricate, nuanced, adept, endeavor.\n"
    "INSTEAD USE: plain direct language. 'Built' not 'spearheaded'. "
    "'Used' not 'leveraged'. 'Improved' not 'elevated'. 'Complex' not 'multifaceted'.\n"
    "FORMATTING: Avoid overusing em dashes (—) and hyphens. Use short, clear sentences. "
    "Vary sentence length. Don't start every bullet with an action verb.\n"
    "COVER LETTER specifically: Write conversationally. Use 'I' naturally. "
    "Show personality. Avoid corporate buzzwords. Sound like someone you'd want to talk to.\n"
)

FRAMING_RULES_TECH_ENG = (
    "IDENTITY FRAMING — CRITICAL (variant: tech_eng):\n"
    "The candidate is an NLP/ML Engineer with MSc CS (NLP) from DCU (First-Class, top 5%) "
    "and a portfolio of AI projects. The 13-year tax-administration background is DOMAIN "
    "CONTEXT supporting RegTech / compliance / financial AI applications — it is NOT the "
    "candidate's professional identity.\n"
    "Rules:\n"
    "- NEVER open the summary with 'X years of tax experience', 'Senior tax officer "
    "transitioning to', or similar tax-first framings.\n"
    "- Open the summary with engineer title + DCU credential + at least one concrete "
    "AI project capability.\n"
    "- Tax-administration experience appears as 'domain depth' or trailing context, "
    "not as identity anchor.\n"
    "- The candidate self-taught Python from 2018 and applied it to workflow-automation "
    "tools at the tax bureau — the career transition is CONTINUOUS, not abrupt. Surface "
    "this when describing tax-bureau experience.\n"
    "- Reference projects by capability ('autonomous job-search copilot', 'neuro-symbolic "
    "legal-reasoning pipeline'), not by brand-name tech stack (avoid 'LangGraph-orchestrated' "
    "etc. in the summary — those belong in skills / project sections).\n"
)

FRAMING_RULES_GRAD = (
    "IDENTITY FRAMING — CRITICAL (variant: grad — graduate-program targets):\n"
    "The candidate is a recent MSc Computer Science (NLP) graduate from DCU "
    "(First-Class Honours, Dean's Honour List, top 5%) applying for graduate-scheme / "
    "junior engineering roles. Treat as new graduate — DO NOT lead with the 13-year prior "
    "career; it makes the candidate look overqualified and outside the target persona.\n"
    "Rules:\n"
    "- Open the summary with 'Recent First-Class MSc Computer Science (NLP) graduate' or "
    "similar grad-anchored phrasing; lead with education + project portfolio.\n"
    "- NEVER state 'X years of experience' in summary or anywhere prominent.\n"
    "- Emphasize: learning speed, willingness to ship, hands-on projects, recent academic "
    "performance, Huawei research as bridge to industry.\n"
    "- Tax-administration experience may appear as 'analytical/professional background' "
    "but stays in the trailing third of the CV with minimal bullets.\n"
    "- Self-taught Python from 2018 — surface as evidence of self-driven learning, not as "
    "professional experience claim.\n"
)

FRAMING_RULES_REGTECH = (
    "IDENTITY FRAMING — CRITICAL (variant: regtech — RegTech / compliance / legal-AI targets):\n"
    "The candidate combines 13 years of regulatory and tax-administration experience "
    "(Shanghai Municipal Taxation Bureau — Golden Tax Project Phases III & IV, national "
    "12366 hotline systems, financial audits) with First-Class MSc CS (NLP) from DCU and "
    "a portfolio of NLP/LLM engineering projects. For this variant the domain depth is "
    "the differentiator — engineering capability AMPLIFIES it.\n"
    "Rules:\n"
    "- Open the summary with the COMBINATION — 'RegTech / Compliance AI Engineer combining "
    "13 years of tax-administration and regulatory experience with NLP and LLM engineering "
    "capability' or similar dual-anchor phrasing.\n"
    "- The 13-year regulatory background is the headline asset, NOT trailing context.\n"
    "- Emphasize: Golden Tax Project (national-scale regulatory infrastructure), 12366 "
    "hotline (national-scale data system), financial audit + anomaly detection.\n"
    "- Pair domain depth with concrete AI capability — neuro-symbolic legal reasoning "
    "with Walkers Global is the strongest bridge project, lead with it.\n"
    "- Skills section MUST include 'Financial Auditing' (under Other) — it is a positive "
    "signal for this variant.\n"
    "- Tax bureau bullets: include all 4 — the regulatory depth is precisely the asset.\n"
)

FRAMING_RULES_BY_VARIANT: dict[str, str] = {
    "grad": FRAMING_RULES_GRAD,
    "tech_eng": FRAMING_RULES_TECH_ENG,
    "regtech": FRAMING_RULES_REGTECH,
}

TARGET_PAGES_BY_VARIANT: dict[str, int] = {
    "grad": 1,
    "tech_eng": 2,
    "regtech": 2,
}

VALID_VARIANTS = tuple(FRAMING_RULES_BY_VARIANT.keys())

ROLE_LEVEL_INSTRUCTIONS: dict[str, str] = {
    "graduate": (
        "ROLE LEVEL: Graduate/Entry-level engineering. Candidate has recent MSc + 13 years "
        "prior non-IT experience (engineer-first framing applies — see FRAMING_RULES).\n"
        "- Do NOT lead with '13+ years of experience' — it looks overqualified.\n"
        "- Lead with engineer title + MSc CS (NLP) DCU First-Class (top 5%) + concrete "
        "AI project capabilities.\n"
        "- Frame career transition as continuous (self-taught Python from 2018, applied "
        "to tax workflow automation; later FreeCodeCamp frontend).\n"
        "- Emphasize: recent education, hands-on projects, learning speed, Huawei research.\n"
        "- Tax bureau: pick only 1 bullet — the self-taught Python/automation one "
        "(bullet index 0) is preferred.\n"
        "- Include more projects (3-4) to demonstrate technical depth.\n"
        "- NEVER exaggerate or invent experience."
    ),
    "junior": (
        "ROLE LEVEL: Junior engineering (engineer-first framing — see FRAMING_RULES).\n"
        "- Balance recent MSc (top 5%) with transferable professional experience.\n"
        "- Don't hide the 13 years but frame as 'professional background' not 'IT experience'.\n"
        "- Frame career transition as continuous (self-taught Python from 2018).\n"
        "- Emphasize hands-on projects, Huawei research, neuro-symbolic pipeline.\n"
        "- Tax bureau: pick 2 bullets — Python automation (index 0) and SQL retrieval "
        "(index 1) first.\n"
        "- Include 2-3 projects.\n"
        "- NEVER exaggerate or invent experience."
    ),
    "mid": (
        "ROLE LEVEL: Mid-level engineering (engineer-first framing — see FRAMING_RULES).\n"
        "- Blend MSc (NLP, top 5%) with depth of professional experience.\n"
        "- 13 years is an asset for RegTech/finance roles — emphasize analytical depth, "
        "scale of systems, combination of domain + technical expertise.\n"
        "- Frame career transition as continuous (self-taught Python from 2018).\n"
        "- Highlight Huawei research and Walkers practicum as recent relevant experience.\n"
        "- Tax bureau: pick 2-3 bullets — surface Python automation (index 0) and SQL "
        "retrieval (index 1) first; add Golden Tax Project (index 2) for scale signal.\n"
        "- Include 2-3 projects.\n"
        "- NEVER exaggerate or invent experience."
    ),
    "senior": (
        "ROLE LEVEL: Senior engineering. Candidate combines MSc CS (NLP) DCU (top 5%) "
        "with 13 years of prior tax-administration domain depth (engineer-first framing "
        "— see FRAMING_RULES; do NOT lead with tax years).\n"
        "- Emphasize: scale of systems built (national 12366 hotline, Golden Tax Project), "
        "end-to-end delivery, technical writing, cross-functional stakeholder communication.\n"
        "- Frame career transition as continuous (self-taught Python from 2018).\n"
        "- Tax bureau: pick 2-3 bullets — Python automation (index 0) and SQL retrieval "
        "(index 1) first, then Golden Tax Project (index 2) for scale.\n"
        "- Include 3 projects showing end-to-end delivery (jobpilot, neuro-symbolic, GliNER).\n"
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
        "- would_shortlist: true/false with reasoning. "
        "IMPORTANT: would_shortlist should be true ONLY if overall_score >= 7. "
        "A score of 5-6 means NOT shortlisted.\n"
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
        response = _call_claude(prompt, timeout=600)
        return _parse_json_response(response)
    except Exception as exc:
        raise RuntimeError(f"CV evaluation failed: {exc}") from exc


def suggest_adjustments(
    cv_data: dict[str, Any],
    job: dict[str, Any],
    evaluation: dict[str, Any],
    master: dict[str, Any],
    role_level: str | None = None,
    missing_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Suggest adjustment diffs based on evaluation feedback. Returns ~30 lines, not a full CV.

    Uses the same adjustment schema as tailor_cv so results can be applied
    programmatically via _apply_adjustments.

    ``missing_keywords`` — objective ATS gaps (from jobpilot.ats). When passed,
    the prompt explicitly prioritizes surfacing these keywords *if they are
    truthfully supported by master_cv or stories*.
    """
    cv_summary = _compact_cv_summary(cv_data, master)
    job_description = job.get("full_description", job.get("description", ""))

    # Available options (abbreviated)
    exp_options = []
    for exp in master["experience"]:
        exp_options.append(f"  [{exp['id']}] {exp['title']} ({exp['dates']}): {len(exp['bullets'])} bullets")
        for i, bullet in enumerate(exp["bullets"]):
            exp_options.append(f"    [{i}] {bullet[:80]}...")
    exp_text = "\n".join(exp_options)

    proj_ids = [p["id"] for p in master["projects"]]

    eval_summary = json.dumps({
        "missing_critical": evaluation.get("keyword_coverage", {}).get("missing_critical", []),
        "suggestions": evaluation.get("suggestions", [])[:5],
        "red_flags": evaluation.get("red_flags", []),
    })

    role_note = ""
    if role_level and role_level in ROLE_LEVEL_INSTRUCTIONS:
        role_note = ROLE_LEVEL_INSTRUCTIONS[role_level] + "\n\n"

    ats_gaps_note = ""
    if missing_keywords:
        ats_gaps_note = (
            "\nATS KEYWORD GAPS (objective — these are literally absent from the CV text):\n"
            f"  {missing_keywords[:15]}\n"
            "If any of these are truthfully supported by the candidate's master CV or stories "
            "(skills list, experience bullets, project descriptions), prioritize selecting the "
            "bullets/projects/skills that surface them. DO NOT invent content to cover a gap — "
            "if master_cv genuinely doesn't support a keyword, leave it missing.\n"
        )

    prompt = (
        f"{TONE_RULES}\n"
        "Suggest CV adjustments based on evaluator feedback.\n\n"
        "RULES: Only select from AVAILABLE OPTIONS. Never invent content.\n\n"
        f"{role_note}"
        f"JOB: {job.get('title', '')} at {job.get('company', '')}\n"
        f"Description: {job_description[:1000]}\n\n"
        f"CURRENT SELECTIONS:\n{cv_summary}\n\n"
        f"AVAILABLE EXPERIENCE:\n{exp_text}\n\n"
        f"AVAILABLE PROJECTS: {proj_ids}\n\n"
        "AVAILABLE SKILLS: "
        f"Languages={master['skills']['languages']}, "
        f"ML_AI={master['skills']['ml_ai']}, "
        f"Tools={master['skills']['tools']}, "
        f"Other={master['skills']['other']}\n\n"
        f"EVALUATOR FEEDBACK:\n{eval_summary}\n"
        f"{ats_gaps_note}\n"
        "Return a JSON object with ONLY the fields that need changing:\n"
        "  summary, include_fudan, experience_bullet_indices, project_ids, skills, include_awards\n"
        "Set fields to null if no change needed. Return ONLY valid JSON, no markdown fences."
    )
    try:
        response = _call_claude(prompt, timeout=600)
        return _parse_json_response(response)
    except Exception as exc:
        raise RuntimeError(f"Adjustment suggestion failed: {exc}") from exc


def auto_tailor_loop(
    job: dict[str, Any],
    stories: list[Story],
    profile: dict[str, Any],
    role_level: str | None = None,
    max_iterations: int = 3,
    progress_cb: Any = None,
    ats_threshold: float = 0.75,
) -> dict[str, Any]:
    """Tailor → score → adjust loop, driven by an objective ATS score.

    The loop converges when:
      - ``ats_score.overall >= ats_threshold`` (the objective gate), OR
      - the LLM recruiter scorer would shortlist AND ATS coverage isn't
        catastrophically low (fallback path for cases the ATS simulator
        under-weights), OR
      - ``max_iterations`` reached, OR
      - ATS score plateaus across an adjustment round.

    ATS gaps (``missing_must``, top ``missing_nice``) are fed back into
    ``suggest_adjustments`` so each iteration can target the actual gap.
    Adjustments still apply to master_cv only, so fabrication is
    structurally impossible.
    """
    # Deferred import to avoid circular dependency (jobpilot.ats imports from
    # this module for the JD Claude-normalization call).
    from jobpilot.ats import ats_score as _ats_score

    master = _load_master_cv()
    jd_text = job.get("full_description") or job.get("description", "")

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
    ats_result = None
    prev_ats = 0.0

    for i in range(max_iterations):
        # Objective signal first — fast, cached, no recruiter-vibes.
        _progress(f"Iteration {i + 1}: computing ATS score...")
        try:
            ats_result = _ats_score(
                cv_data=cv,
                jd_text=jd_text,
                threshold=ats_threshold,
                use_llm=True,
            )
        except Exception as exc:
            _progress(f"ATS scoring failed ({exc}); skipping objective gate this iteration.")
            ats_result = None

        _progress(f"Iteration {i + 1}: running recruiter evaluation...")
        evaluation = evaluate_cv(cv, job, cl)
        llm_score = evaluation.get("overall_score", 0)
        shortlisted = bool(evaluation.get("would_shortlist")) and llm_score >= 7
        evaluation["would_shortlist"] = shortlisted
        if ats_result is not None:
            evaluation["ats_score"] = ats_result.to_dict()

        ats_overall = ats_result.overall if ats_result is not None else 0.0
        iterations.append({
            "iter": i + 1,
            "ats_score": ats_overall,
            "llm_score": llm_score,
            "shortlist": shortlisted,
            "missing_must": ats_result.coverage.missing_must if ats_result else [],
        })

        # Primary exit: objective ATS threshold met.
        if ats_result is not None and ats_result.overall >= ats_threshold:
            _progress(
                f"ATS threshold met at iteration {i + 1} "
                f"(ats={ats_result.overall:.2f} >= {ats_threshold})"
            )
            break

        # Secondary exit: LLM says shortlist AND ATS isn't catastrophic.
        if shortlisted and ats_overall >= 0.5:
            _progress(
                f"Shortlisted at iteration {i + 1} "
                f"(llm={llm_score}/10, ats={ats_overall:.2f})"
            )
            break

        # Plateau: no ATS improvement after an adjustment round → stop wasting calls.
        if i > 0 and ats_overall <= prev_ats:
            _progress(
                f"ATS score plateaued at {ats_overall:.2f} — stopping. "
                "Gaps likely need master_cv or story updates to close."
            )
            break
        prev_ats = ats_overall

        if i < max_iterations - 1:
            _progress(
                f"Iteration {i + 1}: ats={ats_overall:.2f}, llm={llm_score}/10 — "
                "suggesting adjustments targeting ATS gaps..."
            )
            missing_keywords: list[str] = []
            if ats_result is not None:
                missing_keywords = (
                    list(ats_result.coverage.missing_must)
                    + list(ats_result.coverage.missing_nice[:5])
                )
            try:
                adjustments = suggest_adjustments(
                    cv, job, evaluation, master, role_level,
                    missing_keywords=missing_keywords or None,
                )
                if adjustments:
                    cv = _apply_adjustments(master, adjustments, current_cv=cv)
                    if role_level:
                        cv["role_level"] = role_level
            except Exception as exc:
                _progress(f"Adjustment suggestion failed ({exc}), keeping current CV...")

            _progress(f"Iteration {i + 1}: regenerating cover letter...")
            try:
                cl = generate_cover_letter(job, stories, profile, role_level=role_level)
            except Exception:
                pass  # keep existing cover letter
        else:
            _progress(
                f"Max iterations reached (ats={ats_overall:.2f}, llm={llm_score}/10)"
            )

    return {
        "cv": cv,
        "cover_letter": cl,
        "evaluation": evaluation,
        "iterations": iterations,
        "ats": ats_result.to_dict() if ats_result is not None else None,
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

    from datetime import date as _date

    today = _date.today().isoformat()
    search_urls = "\n".join(
        f"  - https://ie.linkedin.com/jobs/search?keywords={q.replace(' ', '%20')}&location={location.replace(' ', '%20')}&f_TPR=r604800"
        for q in queries[:4]
    )
    prompt = (
        f"Search for jobs in {location}, posted in the last {days} days.\n"
        f"Search ALL of these queries (not just the first one):\n"
        + "\n".join(f"  - {q}" for q in queries[:4]) + "\n\n"
        f"Use these LinkedIn search URLs to find jobs:\n{search_urls}\n\n"
        "Also try other job boards if LinkedIn results are limited: Indeed Ireland, "
        "IrishJobs.ie, Glassdoor Ireland.\n\n"
        "Include junior, graduate, and mid-level roles — not just senior.\n"
        "Include roles at large tech companies (Google, Meta, eBay, Stripe, etc.) "
        "AND smaller companies/startups.\n\n"
        f"Today's date is {today}. For the 'posted' field, convert relative times "
        f"(like '2 hours ago', '3 days ago') to absolute dates (like '{today}'). "
        f"If posted today or yesterday, use the actual date.\n\n"
        f"Return up to {limit} jobs as a JSON array: "
        "[{title, company, location, url, source, posted, description}]\n"
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
            posted = item.get("posted", "")
            # If Claude returned a relative time despite instructions, use today
            if posted and not any(c.isdigit() and "-" in posted for c in [posted]):
                # Looks like "2 hours ago" not "2026-04-13" — normalize
                if "ago" in posted.lower() or "just" in posted.lower():
                    posted = _date.today().isoformat()
            jobs.append({
                "id": f"web_{hash(item.get('url', item.get('title', ''))) & 0xFFFFFFFF:08x}",
                "title": title,
                "company": item.get("company", ""),
                "location": item.get("location", location),
                "description": description,
                "url": item.get("url", ""),
                "source": item.get("source", "web"),
                "posted": posted,
                "skills": _extract_skills_from_text(full_text, profile_candidates),
            })
        return jobs
    except Exception as exc:
        raise RuntimeError(f"Web job search failed: {exc}") from exc
