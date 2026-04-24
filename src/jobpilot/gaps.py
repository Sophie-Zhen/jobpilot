"""Skills gap aggregation across all evaluated jobs."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_GAP_PROGRESS_PATH = Path("data/gap_progress.json")


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
