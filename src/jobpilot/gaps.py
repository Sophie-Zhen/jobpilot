"""Skills gap aggregation across all evaluated jobs.

Two complementary signals:

1. ``scan_all_gaps`` — reads ``evaluation.gaps.{quick_fill,hard_gaps}`` from
   the recruiter-eval LLM call. Subjective (Claude decides quick vs hard) but
   carries learning recipes (``how_to_fill``, ``suggested_bullet``).
2. ``scan_ats_gaps`` — reads ``evaluation.ats_score.coverage.{missing_must,
   missing_nice}`` from the objective ATS simulator. Deterministic keyword
   matching against master_cv text. Best signal for "what to add to master_cv".

Both are useful. Use (2) for the master_cv audit (truthful additions) and
(1) for the learning plan (real skill gaps that need study time).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_GAP_PROGRESS_PATH = Path("data/gap_progress.json")
_MASTER_CV_PATH = Path("data/master_cv.json")


# Known technology synonyms — map variations to a canonical key
_TECH_SYNONYMS: dict[str, str] = {
    "react basics": "react", "react fundamentals": "react", "react query basics": "react",
    "typescript basics": "typescript", "typescript": "typescript",
    "docker deployment evidence": "docker", "docker": "docker",
    "tensorflow familiarity": "tensorflow", "tensorflow/keras familiarity": "tensorflow",
    "ci for ml": "ci/cd", "ci/cd for ml workflows": "ci/cd",
    "kubernetes": "kubernetes",
    "mlops fundamentals": "mlops", "model drift monitoring": "mlops",
    "experiment tracking": "mlops",
    "mcp server/client implementation": "mcp", "mcpclient/server": "mcp",
    "mcp": "mcp",
    "github actions / pr review bot basics": "github actions",
    "github webhooks / pr bot integration": "github actions",
    "spring boot basics + minimal rest endpoint": "spring boot",
    "oauth 2.0 / openid connect fundamentals": "oauth",
    "jwt issuance and validation": "auth",
    "tls/mtls basics": "tls",
    "azure openai / azure ai foundry basics": "azure ai",
    "azure data factory": "azure data factory",
    "azure entra id app registration basics": "azure",
    "microsoft fabric": "azure",
    "power automate": "power automate",
    "rest api design framing": "rest api",
    "model serving with fastapi + docker": "docker",
    "demonstrated model-serving endpoint": "model serving",
    "openai spec-driven tool definitions": "openai function calling",
    "function-calling / tool schema via openai spec": "openai function calling",
    "php familiarity": "php",
    "mysql": "mysql",
    "react query basics": "react",
}


def _normalize_skill(skill: str) -> str:
    """Normalize skill name for dedup.

    1. Strip parenthetical qualifiers
    2. Check synonym table for known merges
    3. Fall back to stripped lowercase
    """
    base = re.sub(r"\s*\(.*?\)\s*", "", skill).strip().lower()
    base = base or skill.strip().lower()
    # Check synonym table
    if base in _TECH_SYNONYMS:
        return _TECH_SYNONYMS[base]
    return base


def _normalize_hard_gap(text: str) -> str:
    """Extract key phrase from a hard gap description for dedup."""
    for sep in (" — ", " -- ", " - "):
        if sep in text:
            text = text.split(sep)[0]
            break
    return text.strip().lower()[:80]


def scan_all_gaps(work_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Scan all work files and aggregate gaps by frequency.

    Returns {"quick_fill": [...], "hard_gaps": [...]}, each sorted by frequency desc.
    """
    quick_map: dict[str, dict[str, Any]] = {}
    hard_map: dict[str, dict[str, Any]] = {}

    for work_file in sorted(work_dir.glob("*.json")):
        try:
            data = json.loads(work_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        job = data.get("job", {})
        job_info = {
            "id": job.get("id", work_file.stem),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
        }

        evaluation = data.get("evaluation", {})
        gaps = evaluation.get("gaps", {})
        if not gaps:
            continue

        for qf in gaps.get("quick_fill", []) or []:
            skill = qf.get("skill", "")
            if not skill:
                continue
            key = _normalize_skill(skill)
            if key not in quick_map:
                quick_map[key] = {
                    "skill": skill,
                    "normalized": key,
                    "frequency": 0,
                    "jobs": [],
                    "how_to_fill": "",
                    "suggested_bullet": "",
                    "reasons": [],
                }
            entry = quick_map[key]
            entry["frequency"] += 1
            entry["jobs"].append(job_info)
            # Keep the most descriptive (longest) name variant
            if len(skill) > len(entry["skill"]):
                entry["skill"] = skill
            # Keep the longest how_to_fill and suggested_bullet
            htf = qf.get("how_to_fill", "")
            if len(htf) > len(entry["how_to_fill"]):
                entry["how_to_fill"] = htf
            sb = qf.get("suggested_bullet", "")
            if len(sb) > len(entry["suggested_bullet"]):
                entry["suggested_bullet"] = sb
            reason = qf.get("reason_missing", "")
            if reason:
                entry["reasons"].append(reason)

        for hg in gaps.get("hard_gaps", []) or []:
            if not hg:
                continue
            key = _normalize_hard_gap(hg)
            if key not in hard_map:
                hard_map[key] = {
                    "description": hg,
                    "normalized": key,
                    "frequency": 0,
                    "jobs": [],
                }
            hard_entry = hard_map[key]
            hard_entry["frequency"] += 1
            hard_entry["jobs"].append(job_info)
            # Keep the longest description variant
            if len(hg) > len(hard_entry["description"]):
                hard_entry["description"] = hg

    quick_list = sorted(quick_map.values(), key=lambda x: -x["frequency"])
    hard_list = sorted(hard_map.values(), key=lambda x: -x["frequency"])
    return {"quick_fill": quick_list, "hard_gaps": hard_list}


def load_gap_progress() -> dict[str, Any]:
    """Load gap completion progress from disk."""
    if _GAP_PROGRESS_PATH.exists():
        try:
            return json.loads(_GAP_PROGRESS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"completed": {}}


def save_gap_progress(progress: dict[str, Any]) -> None:
    """Save gap completion progress to disk."""
    _GAP_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _GAP_PROGRESS_PATH.write_text(
        json.dumps(progress, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def mark_gap_completed(
    normalized_skill: str, completed_date: str, story_id: str | None = None,
) -> None:
    """Mark a gap as completed."""
    progress = load_gap_progress()
    progress["completed"][normalized_skill] = {
        "date": completed_date,
        "story_id": story_id or "",
    }
    save_gap_progress(progress)


def is_gap_completed(progress: dict[str, Any], normalized_skill: str) -> bool:
    """Check if a gap has been marked as completed."""
    return normalized_skill in progress.get("completed", {})


# ---------------------------------------------------------------------------
# ATS-aware gap aggregation (objective signal from ats_score.coverage).
# ---------------------------------------------------------------------------

def _master_cv_searchable_text(master_cv_path: Path = _MASTER_CV_PATH) -> str:
    """Flatten master_cv.json to a single searchable text blob.

    Returns empty string if the file isn't readable.
    """
    if not master_cv_path.exists():
        return ""
    try:
        data = json.loads(master_cv_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    parts: list[str] = []
    if data.get("summary"):
        parts.append(data["summary"])
    for exp in data.get("experience", []) or []:
        parts.append(f"{exp.get('title', '')} {exp.get('company', '')}")
        parts.extend(exp.get("bullets", []) or [])
    for proj in data.get("projects", []) or []:
        parts.append(f"{proj.get('title', '')} {proj.get('tech', '')}")
        parts.extend(proj.get("bullets", []) or [])
    skills = data.get("skills", {})
    if isinstance(skills, dict):
        for items in skills.values():
            parts.extend(items or [])
    elif isinstance(skills, list):
        parts.extend(skills)
    for edu in data.get("education", []) or []:
        parts.append(f"{edu.get('degree', '')} {edu.get('institution', '')}")
        parts.extend(edu.get("details", []) or [])
    return "\n".join(p for p in parts if p)


def annotate_with_master_cv(keyword: str, master_cv_text: str) -> str:
    """Classify a missing keyword by whether master_cv supports it.

    Returns one of three labels (action implication in parentheses):

    - ``in_master``: keyword (or any synonym) appears in master_cv text.
      Action: just fix tailoring to surface it; no master_cv edit needed.
    - ``partial``: a token of the keyword appears (e.g. master_cv has
      "testing" but the requirement is "Unit Testing"). Action: tighten
      existing bullets to use the exact phrasing.
    - ``absent``: nothing related is in master_cv. **Manual judgment
      required**: this could be a truthful but unmentioned skill (add a
      bullet/skill from your story bank) OR a genuine learning gap (real
      study time required). The CLI can't tell these apart automatically.
    """
    if not master_cv_text:
        return "absent"

    # Reuse ATS-style alias expansion for synonym-aware matching
    from jobpilot.ats import _expand_to_aliases

    text_lower = master_cv_text.lower()
    text_norm = re.sub(r"[^\w\s./+#-]", " ", text_lower)
    text_norm = re.sub(r"\s+", " ", text_norm).strip()

    for form in _expand_to_aliases(keyword):
        if not form:
            continue
        pattern = r"(?:^|[^a-z0-9])" + re.escape(form) + r"(?:$|[^a-z0-9])"
        if re.search(pattern, text_norm):
            return "in_master"

    # Partial: any single-token component of the keyword shows up.
    # Min length 3 so AWS/GCP/MCP/CI can match if present, with a small
    # stoplist to avoid common 3-char English noise.
    _STOP_TOKENS = {"and", "the", "for", "use", "via", "any", "all", "new"}
    tokens = [
        t for t in re.split(r"[\s./+#-]+", keyword.lower())
        if len(t) >= 3 and t not in _STOP_TOKENS
    ]
    for token in tokens:
        pattern = r"(?:^|[^a-z0-9])" + re.escape(token) + r"(?:$|[^a-z0-9])"
        if re.search(pattern, text_norm):
            return "partial"
    return "absent"


def scan_ats_gaps(
    work_dir: Path,
    master_cv_text: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Aggregate ATS keyword gaps across work files.

    Reads ``evaluation.ats_score.coverage.{missing_must,missing_nice}`` from
    each work file. Files lacking this shape (predating the ATS simulator)
    are skipped — call :func:`recompute_ats_for_stale` first to backfill.

    Returns ``{"missing_must": [...], "missing_nice": [...]}``, each entry
    sorted by frequency desc. Each entry:

        {
            "skill": str,            # canonical name
            "frequency": int,        # number of jobs this is missing in
            "jobs": [{id,title,company}, ...],
            "annotation": "truthful" | "partial" | "hard",
        }
    """
    if master_cv_text is None:
        master_cv_text = _master_cv_searchable_text()

    must_map: dict[str, dict[str, Any]] = {}
    nice_map: dict[str, dict[str, Any]] = {}

    for work_file in sorted(work_dir.glob("*.json")):
        try:
            data = json.loads(work_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        evaluation = data.get("evaluation") or {}
        ats = evaluation.get("ats_score") or {}
        coverage = ats.get("coverage") or {}
        if not coverage:
            continue

        job = data.get("job") or {}
        job_info = {
            "id": job.get("id", work_file.stem),
            "title": job.get("title", ""),
            "company": job.get("company", ""),
        }

        for kw in coverage.get("missing_must", []) or []:
            if not kw:
                continue
            key = _normalize_skill(kw)
            entry = must_map.setdefault(key, {
                "skill": kw, "normalized": key,
                "frequency": 0, "jobs": [], "annotation": "",
            })
            entry["frequency"] += 1
            entry["jobs"].append(job_info)
            if len(kw) > len(entry["skill"]):
                entry["skill"] = kw

        for kw in coverage.get("missing_nice", []) or []:
            if not kw:
                continue
            key = _normalize_skill(kw)
            entry = nice_map.setdefault(key, {
                "skill": kw, "normalized": key,
                "frequency": 0, "jobs": [], "annotation": "",
            })
            entry["frequency"] += 1
            entry["jobs"].append(job_info)
            if len(kw) > len(entry["skill"]):
                entry["skill"] = kw

    for entry in list(must_map.values()) + list(nice_map.values()):
        entry["annotation"] = annotate_with_master_cv(entry["skill"], master_cv_text)

    must_list = sorted(must_map.values(), key=lambda x: -x["frequency"])
    nice_list = sorted(nice_map.values(), key=lambda x: -x["frequency"])
    return {"missing_must": must_list, "missing_nice": nice_list}


def recompute_ats_for_stale(
    work_dir: Path,
    force: bool = False,
    progress_cb: Any = None,
) -> int:
    """Backfill ``evaluation.ats_score`` for work files that predate the simulator.

    Returns the count of files recomputed. Each computation runs ``ats_score``
    once per file; the JD-requirement Claude call is cached after the first
    run per JD, so cost amortizes over repeated runs.
    """
    from jobpilot.ats import ats_score

    def _say(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    count = 0
    files = sorted(work_dir.glob("*.json"))
    for work_file in files:
        try:
            data = json.loads(work_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        evaluation = data.get("evaluation") or {}
        if not force and evaluation.get("ats_score"):
            continue  # already has it

        cv_data = data.get("cv_data")
        job = data.get("job") or {}
        jd_text = job.get("full_description") or job.get("description", "")
        if not cv_data or not jd_text:
            _say(f"skip {work_file.name}: missing cv_data or jd")
            continue

        _say(f"computing ATS for {work_file.name} ({job.get('title', '')[:50]})...")
        try:
            score = ats_score(cv_data=cv_data, jd_text=jd_text, use_llm=True)
        except Exception as exc:
            _say(f"  failed: {exc}")
            continue

        evaluation["ats_score"] = score.to_dict()
        data["evaluation"] = evaluation
        work_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _say(f"  ats={score.overall:.2f}  missing_must={len(score.coverage.missing_must)}")
        count += 1

    return count
