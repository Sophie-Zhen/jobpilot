from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from jobpilot.config import Settings


DAILY_BUDGETS: dict[str, int] = {
    "jsearch": 6,
    "linkedin": 1,
    "active_jobs_db": 1,
    "arbeitnow": 999,
    "remotive": 999,
}

_USAGE_PATH = Path("data/api_usage.json")
_SEEN_JOBS_PATH = Path("data/seen_jobs.json")
_APPLICATIONS_PATH = Path("data/applications.json")
_PIPELINE_JOBS_PATH = Path("data/pipeline_jobs.json")
_DAILY_RESULTS_DIR = Path("data/daily_results")


def _load_usage() -> dict[str, Any]:
    if _USAGE_PATH.exists():
        return json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_usage(usage: dict[str, Any]) -> None:
    _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USAGE_PATH.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")


def _get_today_usage(source: str) -> int:
    usage = _load_usage()
    today = date.today().isoformat()
    return usage.get(today, {}).get(source, 0)


def _record_call(source: str) -> None:
    usage = _load_usage()
    today = date.today().isoformat()
    if today not in usage:
        usage = {today: {}}
    day_data = usage.setdefault(today, {})
    day_data[source] = day_data.get(source, 0) + 1
    _save_usage(usage)


def _is_over_budget(source: str) -> bool:
    budget = DAILY_BUDGETS.get(source, 999)
    return _get_today_usage(source) >= budget


def _http_get_json(url: str, timeout: int = 20, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _extract_skills_from_text(text: str, candidates: list[str]) -> list[str]:
    lowered = text.lower()
    unique = {skill for skill in candidates if skill.lower() in lowered}
    return sorted(unique)


def search_jobs_remotive(settings: Settings, query: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"search": query, "limit": settings.daily_limit})
    url = f"{settings.remotive_api_url}?{params}"
    payload = _http_get_json(url)
    jobs = payload.get("jobs", [])
    profile_candidates = profile.get("skills", []) + profile.get("preferred_keywords", [])
    normalized = []
    for job in jobs[: settings.daily_limit]:
        description = job.get("description", "")
        title = job.get("title", "")
        company = (job.get("company_name") or "").strip() or "Unknown"
        full_text = f"{title} {description}"
        normalized.append(
            {
                "id": f"remotive_{job.get('id')}",
                "title": title,
                "company": company,
                "location": job.get("candidate_required_location", "Remote"),
                "description": description[:1200],
                "skills": _extract_skills_from_text(full_text, profile_candidates),
                "url": job.get("url", ""),
                "source": "remotive",
            }
        )
    return normalized


def _query_tokens(query: str) -> list[str]:
    return [t.strip().lower() for t in query.split() if t.strip()]


def _text_matches_query(text: str, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _matches_profile_locations(job_location: str, profile: dict[str, Any]) -> bool:
    target_locations = [loc.strip().lower() for loc in profile.get("locations", []) if loc.strip()]
    if not target_locations:
        return True
    location_text = (job_location or "").lower()
    city_match = any(target in location_text for target in target_locations)
    country = (profile.get("country") or "").strip().lower()
    if country and city_match:
        return country in location_text
    return city_match


def _contains_excluded_keyword(text: str, profile: dict[str, Any]) -> bool:
    excluded = [kw.strip().lower() for kw in profile.get("excluded_keywords", []) if kw.strip()]
    if not excluded:
        return False
    lowered = text.lower()
    return any(kw in lowered for kw in excluded)


def _load_seen_jobs() -> dict[str, Any]:
    if not _SEEN_JOBS_PATH.exists():
        return {}
    try:
        data = json.loads(_SEEN_JOBS_PATH.read_text(encoding="utf-8"))
        # Prune entries older than 90 days
        cutoff = (date.today().toordinal()) - 90
        pruned = {}
        for job_id, info in data.items():
            try:
                seen_date = date.fromisoformat(info.get("first_seen", ""))
                if seen_date.toordinal() >= cutoff:
                    pruned[job_id] = info
            except (ValueError, TypeError):
                pruned[job_id] = info
        if len(pruned) != len(data):
            _save_seen_jobs(pruned)
        return pruned
    except Exception:
        return {}


def _save_seen_jobs(seen: dict[str, Any]) -> None:
    _SEEN_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SEEN_JOBS_PATH.write_text(
        json.dumps(seen, indent=2) + "\n", encoding="utf-8"
    )


def record_seen_jobs(jobs: list[dict[str, Any]]) -> None:
    seen = _load_seen_jobs()
    today = date.today().isoformat()
    for job in jobs:
        job_id = job.get("id", "")
        if job_id and job_id not in seen:
            seen[job_id] = {
                "first_seen": today,
                "source": job.get("source", ""),
                "title": job.get("title", ""),
            }
    _save_seen_jobs(seen)


def _load_applied_job_ids() -> set[str]:
    if not _APPLICATIONS_PATH.exists():
        return set()
    try:
        apps = json.loads(_APPLICATIONS_PATH.read_text(encoding="utf-8"))
        return {app.get("job_id", "") for app in apps if app.get("job_id")}
    except Exception:
        return set()


def _apply_profile_filters(jobs: list[dict[str, Any]], profile: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    seen = _load_seen_jobs()
    applied = _load_applied_job_ids()
    filtered: list[dict[str, Any]] = []
    for job in jobs:
        job_id = job.get("id", "")
        if job_id in seen or job_id in applied:
            continue
        location = job.get("location", "")
        searchable = f"{job.get('title', '')} {job.get('description', '')}"
        if not _matches_profile_locations(location, profile):
            continue
        if _contains_excluded_keyword(searchable, profile):
            continue
        filtered.append(job)
        if len(filtered) >= limit:
            break
    return filtered


def _rapidapi_headers(settings: Settings, host: str) -> dict[str, str]:
    if not settings.rapidapi_key:
        raise ValueError(f"RAPIDAPI_KEY is missing (needed for {host}).")
    return {
        "X-RapidAPI-Key": settings.rapidapi_key,
        "X-RapidAPI-Host": host,
    }


def search_jobs_jsearch(settings: Settings, query: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    host = "jsearch.p.rapidapi.com"
    headers = _rapidapi_headers(settings, host)
    target_locations = [loc for loc in profile.get("locations", []) if loc]
    location_suffix = f" in {target_locations[0]}, Ireland" if target_locations else ""
    full_query = f"{query}{location_suffix}"
    params = urllib.parse.urlencode({
        "query": full_query,
        "page": "1",
        "num_pages": "1",
        "date_posted": "month",
    })
    url = f"https://{host}/search?{params}"
    payload = _http_get_json(url, headers=headers)
    results = payload.get("data", [])
    profile_candidates = profile.get("skills", []) + profile.get("preferred_keywords", [])
    normalized = []
    for job in results[: settings.daily_limit]:
        description = job.get("job_description", "")
        title = job.get("job_title", "")
        company = (job.get("employer_name") or "").strip() or "Unknown"
        city = job.get("job_city", "")
        country = job.get("job_country", "")
        location = f"{city}, {country}" if city else country
        full_text = f"{title} {description}"
        normalized.append(
            {
                "id": f"jsearch_{job.get('job_id', title[:30])}",
                "title": title,
                "company": company,
                "location": location,
                "description": description[:1200],
                "skills": _extract_skills_from_text(full_text, profile_candidates),
                "url": job.get("job_apply_link", ""),
                "source": "jsearch",
            }
        )
    return normalized


def _extract_location_fantastic(job: dict[str, Any]) -> str:
    derived = job.get("locations_derived")
    if derived and isinstance(derived, list) and derived[0]:
        return str(derived[0])
    cities = job.get("cities_derived", [])
    countries = job.get("countries_derived", [])
    if cities:
        return f"{cities[0]}, {countries[0]}" if countries else cities[0]
    raw = job.get("locations_raw")
    if isinstance(raw, list) and raw:
        addr = raw[0].get("address", {}) if isinstance(raw[0], dict) else {}
        parts = [addr.get("addressLocality", ""), addr.get("addressCountry", "")]
        return ", ".join(p for p in parts if p)
    return ""


def _normalize_fantastic_jobs(
    raw_jobs: list[dict[str, Any]], profile: dict[str, Any], source: str, limit: int,
) -> list[dict[str, Any]]:
    profile_candidates = profile.get("skills", []) + profile.get("preferred_keywords", [])
    normalized = []
    for job in raw_jobs[:limit]:
        title = job.get("title", "")
        company = (job.get("organization") or "").strip() or "Unknown"
        location = _extract_location_fantastic(job)
        specialties = job.get("linkedin_org_specialties", []) or []
        specialty_text = " ".join(specialties) if isinstance(specialties, list) else ""
        full_text = f"{title} {specialty_text}"
        normalized.append(
            {
                "id": f"{source}_{job.get('id') or title[:30]}",
                "title": title,
                "company": company,
                "location": location,
                "description": f"[{company}] {title} — {location}",
                "skills": _extract_skills_from_text(full_text, profile_candidates),
                "url": job.get("url") or job.get("external_apply_url") or "",
                "source": source,
            }
        )
    return normalized


def search_jobs_active_jobs_db(settings: Settings, query: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    host = "active-jobs-db.p.rapidapi.com"
    headers = _rapidapi_headers(settings, host)
    target_locations = [loc for loc in profile.get("locations", []) if loc]
    params: dict[str, str] = {
        "title_filter": f"\"{query}\"",
        "location_filter": f"\"{target_locations[0]}\"" if target_locations else "",
        "country_code": "IE",
    }
    params = {k: v for k, v in params.items() if v}
    url = f"https://{host}/active-ats-7d?{urllib.parse.urlencode(params)}"
    payload = _http_get_json(url, headers=headers, timeout=30)
    raw_jobs = payload if isinstance(payload, list) else payload.get("data", payload.get("jobs", []))
    return _normalize_fantastic_jobs(raw_jobs, profile, "active_jobs_db", settings.daily_limit)


def search_jobs_linkedin(settings: Settings, query: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    host = "linkedin-job-search-api.p.rapidapi.com"
    headers = _rapidapi_headers(settings, host)
    target_locations = [loc for loc in profile.get("locations", []) if loc]
    params: dict[str, str] = {
        "title_filter": f"\"{query}\"",
        "location_filter": f"\"{target_locations[0]}\"" if target_locations else "",
    }
    params = {k: v for k, v in params.items() if v}
    url = f"https://{host}/active-jb-7d?{urllib.parse.urlencode(params)}"
    payload = _http_get_json(url, headers=headers, timeout=30)
    raw_jobs = payload if isinstance(payload, list) else payload.get("data", payload.get("jobs", []))
    return _normalize_fantastic_jobs(raw_jobs, profile, "linkedin", settings.daily_limit)


def search_jobs_arbeitnow(settings: Settings, query: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    payload = _http_get_json(settings.arbeitnow_api_url)
    jobs = payload.get("data", [])
    profile_candidates = profile.get("skills", []) + profile.get("preferred_keywords", [])
    normalized = []
    for job in jobs:
        title = job.get("title", "")
        description = job.get("description", "")
        full_text = f"{title} {description}"
        if not _text_matches_query(full_text, query):
            continue
        company = (job.get("company_name") or "").strip() or "Unknown"
        normalized.append(
            {
                "id": f"arbeitnow_{job.get('slug') or title[:30]}",
                "title": title,
                "company": company,
                "location": ", ".join(job.get("location", [])) if isinstance(job.get("location"), list) else (job.get("location", "")),
                "description": description[:1200],
                "skills": _extract_skills_from_text(full_text, profile_candidates),
                "url": job.get("url", ""),
                "source": "arbeitnow",
            }
        )
        if len(normalized) >= settings.daily_limit:
            break
    return normalized


def _get_provider_fn(name: str):
    return {
        "jsearch": search_jobs_jsearch,
        "active_jobs_db": search_jobs_active_jobs_db,
        "linkedin": search_jobs_linkedin,
        "arbeitnow": search_jobs_arbeitnow,
        "remotive": search_jobs_remotive,
    }.get(name)


_FALLBACK_CHAINS: dict[str, list[str]] = {
    "linkedin": ["linkedin", "active_jobs_db", "jsearch", "arbeitnow"],
    "active_jobs_db": ["active_jobs_db", "linkedin", "jsearch", "arbeitnow"],
    "jsearch": ["jsearch", "linkedin", "active_jobs_db", "arbeitnow"],
    "arbeitnow": ["arbeitnow", "jsearch", "remotive"],
    "remotive": ["remotive", "jsearch", "arbeitnow"],
}


def search_open_jobs_with_fallback(
    settings: Settings, query: str, profile: dict[str, Any]
) -> tuple[list[dict[str, Any]], str]:
    """Try *query* on the primary source; on failure walk the fallback chain."""
    chain = _FALLBACK_CHAINS.get(settings.job_source, [settings.job_source, "arbeitnow"])

    errors: list[str] = []
    for source_name in chain:
        if _is_over_budget(source_name):
            used = _get_today_usage(source_name)
            limit = DAILY_BUDGETS.get(source_name, 999)
            errors.append(f"{source_name}: daily budget exhausted ({used}/{limit})")
            continue
        fn = _get_provider_fn(source_name)
        if fn is None:
            errors.append(f"{source_name}: unknown provider")
            continue
        try:
            _record_call(source_name)
            jobs = fn(settings, query, profile)
            jobs = _apply_profile_filters(jobs, profile, settings.daily_limit)
            if jobs:
                return jobs, f"source={source_name}"
            errors.append(f"{source_name}: empty result after filters")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source_name}: {exc}")
    return [], "all_sources_failed | " + " ; ".join(errors)


def search_jobs_multi_query(
    settings: Settings,
    query_candidates: list[str],
    profile: dict[str, Any],
) -> tuple[list[dict[str, Any]], str, str]:
    """Try multiple queries but be budget-smart.

    Strategy: exhaust all query candidates on the primary (cheapest) source
    before falling back to secondary sources with tighter budgets.
    Returns (jobs, search_info, winning_query).
    """
    chain = _FALLBACK_CHAINS.get(settings.job_source, [settings.job_source, "arbeitnow"])
    errors: list[str] = []

    for source_name in chain:
        if _is_over_budget(source_name):
            used = _get_today_usage(source_name)
            limit = DAILY_BUDGETS.get(source_name, 999)
            errors.append(f"{source_name}: budget exhausted ({used}/{limit})")
            continue
        fn = _get_provider_fn(source_name)
        if fn is None:
            continue

        for query in query_candidates:
            if _is_over_budget(source_name):
                break
            try:
                _record_call(source_name)
                jobs = fn(settings, query, profile)
                jobs = _apply_profile_filters(jobs, profile, settings.daily_limit)
                if jobs:
                    return jobs, f"source={source_name}", query
                errors.append(f"{source_name}({query}): empty")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{source_name}({query}): {exc}")

    return [], "all_sources_failed | " + " ; ".join(errors), query_candidates[0]


# ============================================================
# Pipeline job persistence (shared by CLI + Streamlit)
# ============================================================

def load_pipeline_jobs() -> list[dict[str, Any]]:
    if _PIPELINE_JOBS_PATH.exists():
        try:
            return json.loads(_PIPELINE_JOBS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_pipeline_jobs(jobs: list[dict[str, Any]]) -> None:
    _PIPELINE_JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PIPELINE_JOBS_PATH.write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def merge_jobs(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge new jobs into existing list, deduplicating by job ID."""
    seen_ids = {j["id"] for j in existing}
    merged = list(existing)
    for job in new:
        if job.get("id") and job["id"] not in seen_ids:
            merged.append(job)
            seen_ids.add(job["id"])
    return merged


def score_jobs(jobs: list[dict[str, Any]], profile_skills: set[str],
               preferred_keywords: list[str] | None = None,
               target_roles: list[str] | None = None) -> list[dict[str, Any]]:
    """Score jobs by skill overlap + keyword/role matching with profile."""
    scored = []
    kw_set = {k.lower() for k in (preferred_keywords or [])}
    role_set = {r.lower() for r in (target_roles or [])}
    for job in jobs:
        # Skill overlap (0-1)
        job_skills = set(job.get("skills", []))
        if job_skills:
            skill_score = len(profile_skills.intersection(job_skills)) / len(job_skills)
        else:
            skill_score = 0.0

        # Keyword match against title (0-1)
        title_lower = (job.get("title") or "").lower()
        if kw_set:
            kw_hits = sum(1 for k in kw_set if k in title_lower)
            kw_score = kw_hits / len(kw_set)
        else:
            kw_score = 0.0

        # Role match — bonus if title closely matches a target role (0 or 0.3)
        role_bonus = 0.3 if any(r in title_lower for r in role_set) else 0.0

        # Weighted combination: skills matter most, keywords next, role bonus
        score = round(min(skill_score * 0.5 + kw_score * 0.3 + role_bonus, 1.0), 2)
        scored.append({**job, "score": score})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def enrich_jobs_missing_skills(jobs: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """For jobs with empty skills, extract from title + description using profile skill candidates."""
    candidates = list(set(
        [s.lower() for s in profile.get("skills", [])]
        + [k.lower() for k in profile.get("preferred_keywords", [])]
    ))
    for job in jobs:
        if not job.get("skills"):
            text = f"{job.get('title', '')} {job.get('description', '')}"
            job["skills"] = _extract_skills_from_text(text, candidates)
    return jobs


def scheduled_search(settings: Settings, profile: dict[str, Any]) -> dict[str, Any]:
    """Run a scheduled job search: search → filter → score → merge → save.

    Uses API-based job sources (not Claude web search) for speed and cost.
    Returns a summary dict with counts.
    """
    query_candidates = [r for r in profile.get("target_roles", []) if r][:4]
    if not query_candidates:
        return {"error": "No target roles in profile", "new": 0, "total": 0}

    # Search using API sources
    jobs, search_info, winning_query = search_jobs_multi_query(
        settings, query_candidates, profile,
    )

    # Add date_found to new jobs
    today = date.today().isoformat()
    for job in jobs:
        if "date_found" not in job:
            job["date_found"] = today

    # Score
    profile_skills = set(s.lower() for s in profile.get("skills", []))
    jobs = score_jobs(
        jobs, profile_skills,
        preferred_keywords=profile.get("preferred_keywords"),
        target_roles=profile.get("target_roles"),
    )

    # Merge with existing pipeline
    existing = load_pipeline_jobs()
    merged = merge_jobs(existing, jobs)
    save_pipeline_jobs(merged)

    # Record seen jobs for dedup
    record_seen_jobs(jobs)

    # Save daily results archive
    _DAILY_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = _DAILY_RESULTS_DIR / f"{today}.json"
    daily_path.write_text(
        json.dumps(jobs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    new_count = len(merged) - len(existing)
    return {
        "new": new_count,
        "total": len(merged),
        "found_today": len(jobs),
        "search_info": search_info,
        "query": winning_query,
    }

