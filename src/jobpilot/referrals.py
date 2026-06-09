"""Referral discovery — surface 1st-degree LinkedIn connections at a target company.

Why this exists: referrals are the single biggest lever in a tech job search
(Orosz, *The Pragmatic Engineer Tech Resume*, ch. 2 — ~10x interview rate at some
companies), and they matter even more for a visa-needing candidate, because a
referral jumps the "local candidates first" queue. The advice is to ask for a
referral BEFORE applying.

Data source: the candidate's own LinkedIn ``Connections.csv`` export
(Settings → Data Privacy → Get a copy of your data → Connections). This is the
only legitimate, robust source for first-degree connections — no scraping, no
ToS violation, no fragile automation. Refresh it periodically.

The module is standalone (stdlib only) so it can be used from the CLI, the
Streamlit UI, or the pipeline without pulling in heavy deps.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Connection:
    first_name: str
    last_name: str
    company: str
    position: str = ""
    url: str = ""
    connected_on: str = ""

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


# Mirrors inbox_sync._normalize_company. Kept local on purpose: referrals must not
# import the Gmail module (heavy google-auth deps) just for a 4-line helper.
def _normalize_company(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\b(inc|ltd|llc|gmbh|plc|limited|corporation|corp|technologies|tech|labs)\b\.?", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _company_tokens(s: str) -> set[str]:
    return set(_normalize_company(s).split())


def load_connections(csv_path: str | Path) -> list[Connection]:
    """Parse a LinkedIn Connections.csv export.

    LinkedIn prepends a few "Notes:" preamble lines before the real header row, so
    we scan for the line that starts the ``First Name,...`` header and parse from
    there. Returns [] for a missing or malformed file rather than raising — a
    missing connections file is a normal state, not an error.
    """
    path = Path(csv_path)
    if not path.exists():
        return []

    raw = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    header_idx = next(
        (i for i, line in enumerate(raw) if line.lower().lstrip('"').startswith("first name")),
        None,
    )
    if header_idx is None:
        return []

    out: list[Connection] = []
    for row in csv.DictReader(raw[header_idx:]):
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        if not (first or last):
            continue
        out.append(
            Connection(
                first_name=first,
                last_name=last,
                company=(row.get("Company") or "").strip(),
                position=(row.get("Position") or "").strip(),
                url=(row.get("URL") or "").strip(),
                connected_on=(row.get("Connected On") or "").strip(),
            )
        )
    return out


def find_referrers(company: str, connections: list[Connection]) -> list[Connection]:
    """Connections whose company matches ``company`` (token-subset, order-stable).

    Token-subset matching ("Stripe" ⊆ "Stripe Payments") avoids the false positives
    of naive substring matching ("Meta" in "Metabase"). Either direction counts so
    "Google" matches "Google Ireland" and vice versa.
    """
    target = _company_tokens(company)
    if not target:
        return []
    out: list[Connection] = []
    for c in connections:
        tokens = _company_tokens(c.company)
        if tokens and (target <= tokens or tokens <= target):
            out.append(c)
    return out


def top_companies(connections: list[Connection], n: int = 10) -> list[tuple[str, int]]:
    """Companies where the candidate has the most connections — networking targets.

    Keyed by display company name (first spelling seen), sorted by count desc.
    """
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for c in connections:
        key = _normalize_company(c.company)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        display.setdefault(key, c.company)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(display[k], v) for k, v in ranked[:n]]


def referral_hint(company: str, connections: list[Connection], limit: int = 5) -> str:
    """One-line human-readable referral hint for a company, or '' if none."""
    refs = find_referrers(company, connections)
    if not refs:
        return ""
    shown = ", ".join(
        f"{r.name}" + (f" ({r.position})" if r.position else "") for r in refs[:limit]
    )
    more = f" +{len(refs) - limit} more" if len(refs) > limit else ""
    return f"{len(refs)} connection(s) at {company}: {shown}{more}"
