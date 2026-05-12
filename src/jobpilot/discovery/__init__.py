"""Job discovery sources — Tier 1 (direct ATS polling) and Tier 2 (LinkedIn/Indeed via opencli)."""
from jobpilot.discovery.ats_sources import (
    discover_all,
    fetch_ashby,
    fetch_greenhouse,
    fetch_lever,
    is_dublin_eligible,
)
from jobpilot.discovery.opencli_source import (
    discover_broad,
    get_daily_usage,
    is_over_budget,
    linkedin_search,
    opencli_available,
)

__all__ = [
    # Tier 1
    "discover_all",
    "fetch_greenhouse",
    "fetch_lever",
    "fetch_ashby",
    "is_dublin_eligible",
    # Tier 2
    "discover_broad",
    "linkedin_search",
    "opencli_available",
    "get_daily_usage",
    "is_over_budget",
]
