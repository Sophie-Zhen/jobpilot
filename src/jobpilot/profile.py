from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jobpilot.config import Settings


_MASTER_CV_PATH = Path("data/master_cv.json")


def _load_master_cv() -> dict[str, Any]:
    if _MASTER_CV_PATH.exists():
        return json.loads(_MASTER_CV_PATH.read_text(encoding="utf-8"))
    return {}


def ensure_profile_file(settings: Settings) -> Path:
    profile_path = Path(settings.profile_path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if not profile_path.exists():
        default_profile = {
            "target_roles": ["AI Engineer", "NLP Engineer", "Machine Learning Engineer"],
            "locations": ["Dublin"],
            "country": "Ireland",
            "experience_years": 0,
            "preferred_keywords": [],
            "excluded_keywords": [],
            "daily_limit": 5,
            "job_source": "web",
        }
        profile_path.write_text(
            json.dumps(default_profile, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return profile_path


def load_profile(settings: Settings) -> dict[str, Any]:
    """Load unified profile: master_cv.json for content, profile.json for search config."""
    profile_path = ensure_profile_file(settings)
    search_config = json.loads(profile_path.read_text(encoding="utf-8"))

    master = _load_master_cv()
    contact = master.get("contact", {})
    skills_data = master.get("skills", {})

    # Flatten skills from master CV
    all_skills = (
        skills_data.get("languages", [])
        + skills_data.get("ml_ai", [])
        + skills_data.get("tools", [])
        + skills_data.get("other", [])
    )

    # Merge: contact from master_cv, config from profile.json
    return {
        # Contact (from master_cv.json — single source of truth)
        "name": contact.get("name", ""),
        "preferred_name": contact.get("name", ""),
        "email": contact.get("email", ""),
        "phone": contact.get("phone", ""),
        "linkedin": contact.get("linkedin", ""),
        "github": contact.get("github", ""),
        "location": contact.get("location", ""),
        "visa_status": contact.get("visa", ""),
        # Skills (from master_cv.json)
        "skills": [s.lower() for s in all_skills],
        # Search config (from profile.json)
        **{k: v for k, v in search_config.items()
           if k not in ("name", "email", "phone", "linkedin", "github",
                        "location", "visa_status", "skills", "preferred_name")},
    }
