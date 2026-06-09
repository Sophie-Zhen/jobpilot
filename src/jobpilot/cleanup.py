"""Reclaim disk by deleting tailored output folders that are no longer useful.

A job's CV / cover-letter PDFs (under ``output/{folder}/``) are only worth
keeping while the application is live. Once a job is skipped/dropped (never
applied) or the application is rejected/closed, those artifacts are dead
weight — and they're fully regenerable via ``jobpilot tailor`` anyway.

This module deletes only the ``output/`` artifacts; the job METADATA stays in
``saved.json`` / ``skipped.json`` / ``applications.json`` so market-demand
stats are unaffected.

Keep vs delete:
  - KEEP:   saved jobs, applications with status submitted / interview
  - DELETE: skipped jobs, applications with status rejection / closed_before_apply

Used three ways: the ``jobpilot cleanup`` CLI (sweep the backlog), the bot's
Drop button (delete on tap), and inbox-sync (delete when a rejection lands).
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from jobpilot.config import load_settings

SAVED_PATH = Path("data/saved.json")
SKIPPED_PATH = Path("data/skipped.json")
APPLICATIONS_PATH = Path("data/applications.json")

# Application statuses whose tailored output is no longer worth keeping.
DELETE_STATUSES = {"rejection", "closed_before_apply"}
# Statuses (plus anything saved) whose output must be preserved.
KEEP_STATUSES = {"submitted", "interview"}


def _output_dir() -> Path:
    return Path(load_settings().output_dir)


def _id_suffix(job_id: str) -> str:
    """The trailing token ``_job_folder`` appends to every output folder name.

    Mirrors ``cli._job_folder``: trailing digits of the job id, else the last
    8 chars. Used to match a folder to its job without depending on the
    (possibly since-edited) title slug in the folder name.
    """
    if not job_id:
        return ""
    m = re.search(r"(\d+)$", job_id)
    return m.group(1) if m else job_id[-8:].lstrip("_")


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _folder_job_id(folder: Path) -> str | None:
    """Authoritative job id from a folder's ``state_*.json`` (if present)."""
    for sf in folder.glob("state_*.json"):
        try:
            st = json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        job = st.get("job", st) if isinstance(st, dict) else {}
        if isinstance(job, dict):
            return job.get("id") or job.get("job_id")
        return None
    return None


def _folders_for_job(job_id: str) -> list[Path]:
    """Output folders belonging to ``job_id`` (state-json match, else suffix)."""
    out = _output_dir()
    if not out.is_dir() or not job_id:
        return []
    suffix = _id_suffix(job_id)
    matches: list[Path] = []
    for d in out.iterdir():
        if not d.is_dir():
            continue
        fid = _folder_job_id(d)
        if fid is not None:
            if fid == job_id:
                matches.append(d)
        elif suffix and d.name.endswith(suffix):
            matches.append(d)
    return matches


def delete_output_for(job_id: str, keep_ids: set[str] | None = None) -> int:
    """Delete the output folder(s) for one job. Returns bytes freed.

    No-op if ``job_id`` is in ``keep_ids`` (guards an active application) or
    has no folder on disk. Safe to call from the bot Drop handler / inbox-sync.
    """
    if not job_id or (keep_ids and job_id in keep_ids):
        return 0
    freed = 0
    for folder in _folders_for_job(job_id):
        freed += _dir_size(folder)
        shutil.rmtree(folder, ignore_errors=True)
    return freed


def _keep_ids() -> set[str]:
    keep = {s.get("job_id") for s in _load(SAVED_PATH) if s.get("job_id")}
    for a in _load(APPLICATIONS_PATH):
        if a.get("status") in KEEP_STATUSES and a.get("job_id"):
            keep.add(a["job_id"])
    return keep


def _delete_ids() -> set[str]:
    delete = {s.get("job_id") for s in _load(SKIPPED_PATH) if s.get("job_id")}
    for a in _load(APPLICATIONS_PATH):
        if a.get("status") in DELETE_STATUSES and a.get("job_id"):
            delete.add(a["job_id"])
    return delete


def sweep(dry_run: bool = False) -> dict[str, Any]:
    """Scan ``output/`` once and delete folders for skipped / rejected jobs.

    Keep wins over delete: a folder whose job is also an active application is
    never removed. Folders that can't be resolved to a delete-eligible job are
    left untouched (conservative). Returns a report dict.
    """
    keep_ids = _keep_ids()
    delete_ids = _delete_ids() - keep_ids
    keep_suffixes = {_id_suffix(j) for j in keep_ids}
    delete_by_suffix = {_id_suffix(j): j for j in delete_ids}

    out = _output_dir()
    deleted: list[dict[str, Any]] = []
    freed = 0
    if out.is_dir():
        for d in sorted(out.iterdir()):
            if not d.is_dir():
                continue
            jid = _folder_job_id(d)
            if jid is None:  # legacy folder w/o state json — infer by suffix
                jid = next(
                    (j for suf, j in delete_by_suffix.items() if suf and d.name.endswith(suf)),
                    None,
                )
            if jid is None:
                continue
            if jid in keep_ids or _id_suffix(jid) in keep_suffixes:
                continue
            if jid not in delete_ids:
                continue
            size = _dir_size(d)
            if not dry_run:
                shutil.rmtree(d, ignore_errors=True)
            deleted.append({"folder": d.name, "job_id": jid, "bytes": size})
            freed += size

    return {
        "deleted": deleted,
        "count": len(deleted),
        "bytes_freed": freed,
        "dry_run": dry_run,
    }
