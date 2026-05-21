"""Tier-2 discovery via opencli (LinkedIn search using logged-in Chrome).

Calls the locally-installed opencli CLI via subprocess. Used to catch the
long tail of Dublin jobs that direct ATS polling (Tier 1) misses: roles at
Workday-only companies, big-tech Dublin offices, banks, and discoveries
outside the curated target_companies.json list entirely.

Anti-detection design (Sophie's rule):
  - Daily call budget capped (default 15)
  - Random delay between calls (5-30 seconds) to break timing patterns
  - Calls counted in data/api_usage.json under "opencli" key
  - For cron scheduling: add random 0-90min lead time at the cron layer,
    NOT here — this module is sync-call-and-return.

opencli limitation:
  - --location flag breaks Voyager API with HTTP 400. Workaround: encode
    location in the keyword string ("AI Engineer Dublin") and let the
    is_dublin_eligible() filter discard non-Dublin results.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from jobpilot.discovery.ats_sources import _slugify, _today, is_dublin_eligible

# ---------------------------------------------------------------------------
# Paths + config
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OPENCLI_BIN = _PROJECT_ROOT / "node_modules" / ".bin" / "opencli"
_USAGE_PATH = Path("data/api_usage.json")
_USAGE_KEY = "opencli"

DAILY_BUDGET_DEFAULT = 20
DELAY_MIN_SEC = 5
DELAY_MAX_SEC = 30


def _resolve_node22_bin() -> str:
    """Find the latest nvm-installed Node 22.x bin dir."""
    nvm_dir = Path.home() / ".nvm" / "versions" / "node"
    if not nvm_dir.exists():
        raise RuntimeError(
            "nvm not found at ~/.nvm. Install Node 22 (e.g. `nvm install 22`) "
            "before running opencli-based discovery."
        )
    candidates = sorted(nvm_dir.glob("v22.*"), reverse=True)
    if not candidates:
        raise RuntimeError(
            "No Node 22.x in ~/.nvm/versions/node/. Run: nvm install 22"
        )
    return str(candidates[0] / "bin")


def opencli_available() -> bool:
    """True iff opencli is installed locally and Node 22 is present."""
    if not _OPENCLI_BIN.exists():
        return False
    try:
        _resolve_node22_bin()
    except RuntimeError:
        return False
    return True


# ---------------------------------------------------------------------------
# Daily budget tracking (anti-detection)
# ---------------------------------------------------------------------------

def _load_usage() -> dict[str, Any]:
    if not _USAGE_PATH.exists():
        return {}
    try:
        return json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_usage(usage: dict[str, Any]) -> None:
    _USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USAGE_PATH.write_text(
        json.dumps(usage, indent=2) + "\n", encoding="utf-8"
    )


def get_daily_usage() -> int:
    today = date.today().isoformat()
    return _load_usage().get(today, {}).get(_USAGE_KEY, 0)


def _record_call() -> None:
    today = date.today().isoformat()
    usage = _load_usage()
    day_data = usage.setdefault(today, {})
    day_data[_USAGE_KEY] = day_data.get(_USAGE_KEY, 0) + 1
    _save_usage(usage)


def is_over_budget(budget: int = DAILY_BUDGET_DEFAULT) -> bool:
    return get_daily_usage() >= budget


# ---------------------------------------------------------------------------
# opencli subprocess wrapper
# ---------------------------------------------------------------------------

def _run_opencli(args: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Run opencli with Node 22 in PATH. Returns (returncode, stdout, stderr)."""
    env = os.environ.copy()
    env["PATH"] = _resolve_node22_bin() + os.pathsep + env.get("PATH", "")
    # Suppress opencli's "Update available: vX → vY" trailer that gets
    # appended to stdout — it contaminates JSON output and breaks
    # json.loads() in callers. NO_UPDATE_NOTIFIER is the npm community
    # convention recognized by the `update-notifier` library opencli uses.
    env["NO_UPDATE_NOTIFIER"] = "1"
    # Extend opencli's per-command 60s default. Patched linkedin search
    # with engagement helpers (jitter + scroll + dwell) + --details detail
    # fetches per result needs more headroom. 180s lets ~3-5 JDs/query
    # complete without hitting global timeout.
    env.setdefault("OPENCLI_BROWSER_COMMAND_TIMEOUT", "180")
    try:
        result = subprocess.run(
            [str(_OPENCLI_BIN), *args],
            env=env, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def _normalize_linkedin_result(j: dict[str, Any]) -> dict[str, Any] | None:
    """Convert one opencli linkedin search result to jobpilot Job schema.
    Returns None if it fails the Dublin filter.

    When the search was run with --details=true, opencli returns the full
    job description. The field name is not documented, so we try several
    plausible aliases and keep the first non-empty one.
    """
    location = j.get("location", "")
    eligible, reason = is_dublin_eligible(location)
    if not eligible:
        return None

    title = j.get("title", "")
    company = (j.get("company") or "Unknown").strip()
    url = j.get("url", "")

    # Stable ID from LinkedIn job ID embedded in URL
    url_id = ""
    if "/jobs/view/" in url:
        url_id = url.split("/jobs/view/")[-1].split("/")[0].split("?")[0]
    if not url_id:
        url_id = _slugify(f"{company}-{title}")

    # Try every plausible field name from opencli --details output.
    description = ""
    for key in ("description", "jobDescription", "body", "content", "details", "fullDescription"):
        v = j.get(key)
        if isinstance(v, str) and v.strip():
            description = v.strip()
            break

    return {
        "id": f"opencli_linkedin_{url_id}",
        "title": title,
        "company": company,
        "location": location,
        "description": description,
        "skills": [],
        "url": url,
        "source": "opencli:linkedin:search",
        "ats_type": "linkedin_search",
        "posted_at": j.get("listed", ""),
        "dublin_match": reason,
        "date_found": _today(),
    }


def linkedin_search(
    query: str,
    limit: int = 25,
    date_posted: str = "week",
    timeout: int = 240,
    budget: int = DAILY_BUDGET_DEFAULT,
    with_details: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    """Run one opencli LinkedIn search, return (jobs, error).

    Records the call in data/api_usage.json BEFORE invoking (so a hung
    subprocess still counts — fail loud rather than silently retry).
    ``date_posted`` ∈ {"any", "month", "week", "24h"}.
    ``with_details=True`` adds ``--details`` so each result carries the
    full JD body (slower per result, but eliminates the post-hoc
    fetch_full_jd round-trip later).
    """
    if not opencli_available():
        return [], "opencli not installed locally — run `npm install` in project root"
    if is_over_budget(budget):
        return [], f"daily budget exhausted ({get_daily_usage()}/{budget})"

    _record_call()
    # --window foreground is REQUIRED for --details=true. opencli's default
    # windowMode is "background", and Chrome throttles JavaScript execution
    # in background tabs. LinkedIn's JD pages use client-side React hydration
    # to render the "About the job" section — when the tab is throttled, the
    # section never appears and detail fetch fails with "Text not found:
    # About the job". Foreground mode unblocks hydration. Verified 2026-05-21.
    args = ["linkedin", "search", query,
            "--limit", str(limit),
            "--date-posted", date_posted,
            "--window", "foreground"]
    if with_details:
        args += ["--details", "true"]
    args += ["-f", "json"]
    rc, stdout, stderr = _run_opencli(args, timeout=timeout)
    if rc != 0:
        msg = (stderr or stdout or "unknown")[:300].strip()
        return [], f"opencli rc={rc}: {msg}"

    # Use raw_decode to tolerate trailing non-JSON output. opencli
    # occasionally appends an "Update available: vX → vY" notice from the
    # update-notifier npm library after its JSON payload; NO_UPDATE_NOTIFIER
    # in env suppresses most cases but the library's cooldown semantics make
    # that imperfect. raw_decode parses JSON from the start of the string
    # and ignores whatever comes after.
    try:
        raw, _ = json.JSONDecoder().raw_decode(stdout.lstrip())
    except json.JSONDecodeError as exc:
        return [], f"opencli stdout not JSON: {exc}"

    if not isinstance(raw, list):
        # opencli error envelopes are objects like {ok: false, error: ...}
        err = raw.get("error", {}).get("message", "unexpected non-list response") if isinstance(raw, dict) else "unexpected shape"
        return [], f"opencli envelope: {err}"

    jobs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_linkedin_result(item)
        if normalized:
            jobs.append(normalized)
    return jobs, None


# ---------------------------------------------------------------------------
# Multi-query driver — random delays, budget cap
# ---------------------------------------------------------------------------

def discover_broad(
    queries: list[str],
    limit_per_query: int = 25,
    date_posted: str = "week",
    delay_range: tuple[int, int] = (DELAY_MIN_SEC, DELAY_MAX_SEC),
    budget: int = DAILY_BUDGET_DEFAULT,
    progress_cb: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run a series of LinkedIn searches with random inter-call jitter.

    Stops early if the daily budget is exhausted. Returns ``(jobs, stats)``.
    Within ``jobs``, duplicates across queries are dropped by ``id``.
    """
    def _say(m: str) -> None:
        if progress_cb:
            progress_cb(m)

    if not opencli_available():
        return [], {
            "error": "opencli not available",
            "queries_attempted": 0,
            "per_query": {},
            "total_jobs": 0,
            "daily_used": get_daily_usage(),
            "daily_budget": budget,
        }

    seen_ids: set[str] = set()
    all_jobs: list[dict[str, Any]] = []
    per_query: dict[str, dict[str, Any]] = {}
    queries_attempted = 0

    for i, q in enumerate(queries):
        used = get_daily_usage()
        if used >= budget:
            _say(f"  budget exhausted ({used}/{budget}); skipping {len(queries) - i} remaining queries")
            break

        if i > 0:
            delay = random.uniform(*delay_range)
            _say(f"  ... sleep {delay:.1f}s")
            time.sleep(delay)

        _say(f"  search [{used + 1}/{budget}]: {q!r}")
        jobs, err = linkedin_search(q, limit=limit_per_query, date_posted=date_posted, budget=budget)
        queries_attempted += 1
        if err:
            per_query[q] = {"count": 0, "error": err}
            _say(f"    ERR: {err}")
            continue

        new = 0
        for j in jobs:
            if j["id"] in seen_ids:
                continue
            seen_ids.add(j["id"])
            all_jobs.append(j)
            new += 1
        per_query[q] = {"count": len(jobs), "new_after_dedup": new, "error": None}
        _say(f"    -> {len(jobs)} Dublin-eligible ({new} new after dedup)")

    return all_jobs, {
        "queries_attempted": queries_attempted,
        "queries_total": len(queries),
        "per_query": per_query,
        "total_jobs": len(all_jobs),
        "daily_used": get_daily_usage(),
        "daily_budget": budget,
    }
