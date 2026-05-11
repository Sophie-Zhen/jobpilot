"""Direct ATS polling for companies in data/target_companies.json.

Polls public Greenhouse/Lever/Ashby job-board JSON endpoints for every active
Tier-1 company. Filters at intake for Dublin / Ireland / Remote-EMEA eligibility,
because Sophie's Stamp 1G locks the scope to Dublin-payable roles.

Why direct ATS over aggregator APIs:
  - Faster: jobs publish here first; LinkedIn/Indeed sync minutes-to-hours later
  - Free: public JSON, no API key, no TOS risk
  - Higher coverage: some senior/specialised roles never hit aggregators
  - Pre-vetted: only polls companies on target_companies.json, not the long tail
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

_TARGET_COMPANIES_PATH = Path("data/target_companies.json")

# ---------------------------------------------------------------------------
# Dublin / Dublin-remote filter
# ---------------------------------------------------------------------------

DUBLIN_KW: tuple[str, ...] = ("dublin", "ireland")

REMOTE_OK_KW: tuple[str, ...] = (
    "remote - emea", "remote (emea)", "remote, emea", "remote-emea",
    "remote - europe", "remote (europe)", "remote-europe", "europe remote",
    "remote - eu", "remote (eu)", "remote eu",
    "remote - worldwide", "remote (worldwide)", "worldwide remote",
    "remote - global", "remote (global)", "remote, global",
    "anywhere", "fully remote", "remote - anywhere",
)

# Strong signals that the role is NOT Ireland-payable
HARD_NEGATIVE_KW: tuple[str, ...] = (
    "remote - united states", "remote - us", "remote (us)", "remote, us",
    "remote - canada", "remote - north america", "us remote",
    "remote in us", "remote in usa",
)


def is_dublin_eligible(location: str) -> tuple[bool, str]:
    """Return (eligible, reason).

    reason ∈ {"dublin", "remote_emea", "us_or_na_only", "other_location"}.
    """
    s = (location or "").lower()
    for p in HARD_NEGATIVE_KW:
        if p in s:
            return False, "us_or_na_only"
    for p in DUBLIN_KW:
        if p in s:
            return True, "dublin"
    for p in REMOTE_OK_KW:
        if p in s:
            return True, "remote_emea"
    return False, "other_location"


# ---------------------------------------------------------------------------
# HTTP + helpers
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: int = 60) -> Any:
    """GET ``url``, parse JSON. One retry on TimeoutError/URLError to absorb
    transient slowness (some boards like OpenAI's are large and occasionally
    take 30+ seconds to respond)."""
    headers = {"User-Agent": "jobpilot/0.2", "Accept": "application/json"}
    last_exc: Exception | None = None
    for _attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except (TimeoutError, urllib.error.URLError) as exc:
            last_exc = exc
            continue
    assert last_exc is not None
    raise last_exc


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", _HTML_TAG_RE.sub(" ", text)).strip()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:40].strip("-")


def _normalize_id(company_slug: str, source_id: str | None, title: str) -> str:
    key = str(source_id) if source_id else _slugify(title)
    return f"{company_slug}_{key}"


def _today() -> str:
    return date.today().isoformat()


def _ms_to_iso(ms: int | None) -> str:
    if not ms:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


# ---------------------------------------------------------------------------
# ATS clients — each returns a list of Job records in jobpilot schema
# ---------------------------------------------------------------------------

def fetch_greenhouse(slug: str, company_name: str) -> list[dict[str, Any]]:
    """Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _http_get_json(url)
    out: list[dict[str, Any]] = []
    for j in data.get("jobs", []) or []:
        location = (j.get("location") or {}).get("name", "")
        eligible, reason = is_dublin_eligible(location)
        if not eligible:
            continue
        title = j.get("title", "")
        out.append({
            "id": _normalize_id(slug, str(j.get("id", "")), title),
            "title": title,
            "company": company_name,
            "location": location,
            "description": _strip_html(j.get("content", "") or "")[:4000],
            "skills": [],
            "url": j.get("absolute_url", ""),
            "source": f"ats:greenhouse:{slug}",
            "ats_type": "greenhouse",
            "company_slug": slug,
            "posted_at": j.get("updated_at") or j.get("first_published") or "",
            "dublin_match": reason,
            "date_found": _today(),
        })
    return out


def fetch_lever(slug: str, company_name: str) -> list[dict[str, Any]]:
    """Lever: https://api.lever.co/v0/postings/{slug}?mode=json"""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _http_get_json(url)
    out: list[dict[str, Any]] = []
    items = data if isinstance(data, list) else []
    for j in items:
        cats = j.get("categories") or {}
        location = cats.get("location", "")
        eligible, reason = is_dublin_eligible(location)
        if not eligible:
            continue
        title = j.get("text", "")
        desc = j.get("descriptionPlain") or _strip_html(j.get("description", "") or "")
        out.append({
            "id": _normalize_id(slug, j.get("id"), title),
            "title": title,
            "company": company_name,
            "location": location,
            "description": (desc or "")[:4000],
            "skills": [],
            "url": j.get("hostedUrl") or j.get("applyUrl", ""),
            "source": f"ats:lever:{slug}",
            "ats_type": "lever",
            "company_slug": slug,
            "posted_at": _ms_to_iso(j.get("createdAt")),
            "dublin_match": reason,
            "date_found": _today(),
        })
    return out


def fetch_ashby(slug: str, company_name: str) -> list[dict[str, Any]]:
    """Ashby: https://api.ashbyhq.com/posting-api/job-board/{slug}"""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false"
    data = _http_get_json(url)
    out: list[dict[str, Any]] = []
    for j in data.get("jobs", []) or []:
        primary = j.get("location", "") or j.get("locationName", "")
        secondary = j.get("secondaryLocations", []) or []
        sec_str = " / ".join(
            s.get("location", "") if isinstance(s, dict) else str(s) for s in secondary
        )
        combined = f"{primary} / {sec_str}" if sec_str else primary
        eligible, reason = is_dublin_eligible(combined)
        if not eligible:
            continue
        title = j.get("title", "")
        desc = _strip_html(j.get("descriptionHtml", "") or "") or j.get("descriptionPlain", "")
        out.append({
            "id": _normalize_id(slug, j.get("id"), title),
            "title": title,
            "company": company_name,
            "location": combined,
            "description": (desc or "")[:4000],
            "skills": [],
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "source": f"ats:ashby:{slug}",
            "ats_type": "ashby",
            "company_slug": slug,
            "posted_at": j.get("publishedAt", "") or j.get("updatedAt", ""),
            "dublin_match": reason,
            "date_found": _today(),
        })
    return out


_FETCHERS: dict[str, Callable[[str, str], list[dict[str, Any]]]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


# ---------------------------------------------------------------------------
# Driver — read target_companies.json, poll all active Tier-1 in parallel
# ---------------------------------------------------------------------------

def _load_active_tier1(path: Path = _TARGET_COMPANIES_PATH) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        c for c in (data.get("active") or [])
        if c.get("tier") == 1 and c.get("ats") in _FETCHERS and c.get("ats_slug")
    ]


def discover_all(
    target_path: Path = _TARGET_COMPANIES_PATH,
    max_workers: int = 8,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Poll every active Tier-1 company in parallel.

    Returns ``(jobs, stats)``. ``jobs`` is a flat list of Job records that
    already passed the Dublin/Remote-EMEA filter. ``stats`` has per-company
    counts and any errors.
    """
    targets = _load_active_tier1(target_path)

    def _say(m: str) -> None:
        if progress_cb:
            progress_cb(m)

    def _one(co: dict[str, Any]) -> tuple[str, str, str, list[dict[str, Any]], str | None]:
        slug, name, ats = co["ats_slug"], co["name"], co["ats"]
        try:
            jobs = _FETCHERS[ats](slug, name)
            return slug, name, ats, jobs, None
        except urllib.error.HTTPError as exc:
            return slug, name, ats, [], f"HTTP {exc.code}: {exc.reason}"
        except urllib.error.URLError as exc:
            return slug, name, ats, [], f"URL error: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            return slug, name, ats, [], f"{type(exc).__name__}: {exc}"

    all_jobs: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "per_company": {},
        "errors": [],
        "companies_polled": len(targets),
        "polled_at": datetime.now(tz=timezone.utc).isoformat(),
    }

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_one, c) for c in targets]
        for fut in as_completed(futures):
            slug, name, ats, jobs, err = fut.result()
            stats["per_company"][slug] = {
                "name": name, "ats": ats, "count": len(jobs), "error": err,
            }
            if err:
                stats["errors"].append(f"{slug}: {err}")
                _say(f"  ERR  {slug:18} ({ats}): {err}")
            else:
                _say(f"  ok   {slug:18} ({ats}): {len(jobs):3} Dublin-eligible")
            all_jobs.extend(jobs)

    stats["total_jobs"] = len(all_jobs)
    return all_jobs, stats
