"""Resume relevance + parseability check (formerly framed as an "ATS simulator").

Reality check (Orosz, *The Pragmatic Engineer Tech Resume*, ch. 2 & 8): for tech
roles, ATSes do NOT auto-reject resumes — filtering is done by a human in a 7-second
scan, and "ATS optimization" is largely a myth sold by resume services. So this
module is NOT about beating a bot. It measures whether a CV will read as *relevant*
to that human scanner, and guards against the one thing recruiters genuinely
penalize: keyword stuffing.

Four measurable components:

1. Keyword coverage — how much of the JD's vocabulary appears in the CV text,
   weighted must_have vs nice_to_have. A relevance proxy, not a pass/fail gate.
2. Stuffing penalty — over-repeating a JD keyword to inflate coverage reads as
   spam to a human screener, so it subtracts from the score.
3. PDF parseability — can pypdf recover structured fields from a rendered CV?
   (Some pipelines do parse PDFs; clean extraction is still worth ensuring.)
4. Format audit — multi-column / tables / text-in-images scramble extraction.

Aim for the target band, not a perfect score: once coverage is solid (~0.75+),
pushing higher usually means stuffing, which hurts more than it helps.

The module is intentionally standalone — no LangGraph, no Jinja, no Streamlit.
It can be invoked inside the auto-tailor loop (cv_data dict in hand, no PDF
needed yet) or as a post-render gate (PDF already on disk).

CLI: ``python -m jobpilot.ats <cv.json|cv.pdf> <jd.txt>``
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except ImportError:
    _HAS_PDFPLUMBER = False


# ---------------------------------------------------------------------------
# Synonym map — canonical form for keyword matching.
# Extend as new mismatches surface; keep lowercase, canonical form on the left.
# ---------------------------------------------------------------------------
SYNONYMS: dict[str, set[str]] = {
    "python": {"python3", "py"},
    "javascript": {"js", "ecmascript"},
    "typescript": {"ts"},
    "node.js": {"node", "nodejs"},
    "pytorch": {"torch"},
    "tensorflow": {"tf"},
    "postgresql": {"postgres", "psql"},
    "mongodb": {"mongo"},
    "kubernetes": {"k8s"},
    "amazon web services": {"aws"},
    "google cloud platform": {"gcp"},
    "machine learning": {"ml"},
    "natural language processing": {"nlp"},
    "large language model": {"llm", "llms"},
    "retrieval augmented generation": {"rag"},
    "continuous integration": {"ci"},
    "ci/cd": {"cicd", "ci cd"},
    "rest": {"restful", "rest api", "rest apis"},
    "graphql": {"gql"},
    "spring boot": {"springboot"},
    "react": {"reactjs", "react.js"},
    "software development": {"software engineering"},
}


def _build_alias_index() -> dict[str, str]:
    """Flatten SYNONYMS into alias → canonical lookup."""
    index: dict[str, str] = {}
    for canonical, aliases in SYNONYMS.items():
        index[canonical] = canonical
        for alias in aliases:
            index[alias] = canonical
    return index


_ALIAS = _build_alias_index()


def _aliases_for(canonical: str) -> set[str]:
    """Return the full equivalence class for a canonical form (itself + aliases)."""
    return {canonical} | SYNONYMS.get(canonical, set())


def normalize(token: str) -> str:
    """Lowercase, strip punctuation, resolve synonyms to canonical form."""
    t = token.lower().strip()
    t = re.sub(r"[^\w\s./+#-]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return _ALIAS.get(t, t)


def _expand_to_aliases(keyword: str) -> set[str]:
    """All forms to search for when looking up a keyword in CV text.

    Example: ``"LLMs"`` expands to ``{"large language model", "llm", "llms"}``
    so a CV mentioning "LLM-powered" matches a JD requirement of "LLMs".
    """
    canonical = normalize(keyword)
    # Also include the raw lowercased form — handles cases where the input
    # keyword itself isn't in the synonym map.
    raw = keyword.lower().strip().rstrip(".,;:")
    return _aliases_for(canonical) | {canonical, raw}


# ---------------------------------------------------------------------------
# CV text extraction — works on both cv_data dict and rendered PDF.
# ---------------------------------------------------------------------------
def cv_data_to_text(cv_data: dict[str, Any]) -> str:
    """Flatten a tailored cv_data dict into a single searchable text blob.

    Mirrors the flattening in llm.py:evaluate_cv so coverage scoring reflects
    what an ATS parser would see after rendering.
    """
    parts: list[str] = []
    if cv_data.get("summary"):
        parts.append(cv_data["summary"])

    for exp in cv_data.get("experience", []):
        parts.append(f"{exp.get('title', '')} {exp.get('company', '')}")
        parts.extend(exp.get("bullets", []))

    for proj in cv_data.get("projects", []):
        parts.append(f"{proj.get('title', '')} {proj.get('tech', '')}")
        parts.extend(proj.get("bullets", []))

    skills = cv_data.get("skills", {})
    if isinstance(skills, dict):
        for items in skills.values():
            parts.extend(items or [])
    elif isinstance(skills, list):
        parts.extend(skills)

    for edu in cv_data.get("education", []):
        parts.append(f"{edu.get('degree', '')} {edu.get('institution', '')}")
        parts.extend(edu.get("details", []) or [])

    return "\n".join(p for p in parts if p)


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract all text from a PDF using pypdf (same backend many ATS use)."""
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------------
# JD requirement extraction.
# ---------------------------------------------------------------------------
_STOPWORDS = {
    "the", "and", "for", "with", "you", "are", "will", "our", "your", "have",
    "this", "that", "from", "into", "their", "they", "them", "who", "what",
    "has", "had", "been", "being", "a", "an", "of", "to", "in", "on", "at",
    "by", "is", "it", "as", "or", "be", "we", "us", "but", "not", "all",
    "can", "any", "may", "new", "use", "using", "work", "role", "team",
    "help", "some", "more", "most", "across", "within", "through", "well",
    "strong", "great", "good", "best", "lead", "leads", "able", "ability",
    "etc", "like", "including", "include", "e.g", "i.e", "such",
}

_SKILL_PATTERNS = [
    # "X years of Y"
    re.compile(r"\b\d+\+?\s*years?\s+(?:of\s+)?(?:experience\s+(?:with\s+|in\s+)?)?([A-Z][A-Za-z0-9.+#/\- ]{1,40})", re.IGNORECASE),
    # "experience with/in X"
    re.compile(r"\bexperience\s+(?:with|in|using)\s+([A-Z][A-Za-z0-9.+#/\- ]{1,40})"),
    # "proficient/familiar with X"
    re.compile(r"\b(?:proficient|familiar|skilled|hands-on)\s+(?:with|in)\s+([A-Z][A-Za-z0-9.+#/\- ]{1,40})", re.IGNORECASE),
    # "knowledge of X"
    re.compile(r"\bknowledge\s+of\s+([A-Z][A-Za-z0-9.+#/\- ]{1,40})", re.IGNORECASE),
]

# Capitalized tech tokens — conservative match for named technologies.
_TECH_TOKEN = re.compile(r"\b([A-Z][A-Za-z0-9]{1,}(?:[.+#/-][A-Za-z0-9]+)*)\b")


def _regex_candidates(jd_text: str) -> set[str]:
    """Pull candidate skill mentions from the JD via pattern matching.

    Intentionally over-recalls; the Claude pass (or caller) filters.
    """
    candidates: set[str] = set()
    for pattern in _SKILL_PATTERNS:
        for match in pattern.finditer(jd_text):
            phrase = match.group(1).strip().rstrip(".,;:")
            if 2 <= len(phrase) <= 40:
                candidates.add(phrase)

    # Capitalized tech tokens (single words, 3+ chars)
    for match in _TECH_TOKEN.finditer(jd_text):
        token = match.group(1)
        if len(token) >= 3 and token.lower() not in _STOPWORDS:
            candidates.add(token)

    return candidates


@dataclass
class JDRequirements:
    must_have: list[str] = field(default_factory=list)
    nice_to_have: list[str] = field(default_factory=list)
    source: str = "regex"  # "regex" or "llm"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cache_path(jd_text: str) -> Path:
    digest = hashlib.sha256(jd_text.encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(__file__).resolve().parent.parent.parent / "data" / "jd_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.json"


def extract_jd_requirements(
    jd_text: str,
    use_llm: bool = True,
    use_cache: bool = True,
) -> JDRequirements:
    """Extract must-have and nice-to-have skills from a job description.

    - Regex pre-pass surfaces candidates (cheap, deterministic).
    - Single Claude call (optional) splits them into must vs nice and normalizes.
    - Result is cached by JD hash; repeated calls on the same JD are free.
    """
    if not jd_text or len(jd_text.strip()) < 50:
        return JDRequirements()

    cache_file = _cache_path(jd_text)
    if use_cache and cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return JDRequirements(
                must_have=data.get("must_have", []),
                nice_to_have=data.get("nice_to_have", []),
                source=data.get("source", "cache"),
            )
        except Exception:
            pass

    candidates = _regex_candidates(jd_text)
    if not use_llm:
        # Fall back: treat everything as nice_to_have; caller can promote.
        result = JDRequirements(
            must_have=[],
            nice_to_have=sorted(candidates),
            source="regex",
        )
        if use_cache:
            cache_file.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return result

    # LLM normalization pass — deferred import to keep ats.py importable without
    # a working Claude CLI (for tests that skip LLM calls).
    from jobpilot.llm import _call_claude, _parse_json_response

    prompt = (
        "Extract the required and desired skills/technologies from this job "
        "description. Split them into two lists:\n"
        "- must_have: hard requirements (explicitly required, non-negotiable, "
        "or listed under 'required' / 'must have')\n"
        "- nice_to_have: preferred or bonus skills (listed under 'nice to have', "
        "'preferred', 'bonus', or implied but not mandatory)\n\n"
        "Rules:\n"
        "- Use canonical names (e.g. 'Python' not 'Python 3', 'Kubernetes' not 'k8s')\n"
        "- Focus on concrete skills/technologies/tools, not soft skills\n"
        "- Deduplicate — each skill in at most one list\n"
        "- If uncertain, put it in nice_to_have\n\n"
        "Candidate phrases surfaced by regex (may include noise):\n"
        f"{sorted(candidates)}\n\n"
        f"Job description:\n{jd_text[:3000]}\n\n"
        'Return ONLY JSON: {"must_have": [...], "nice_to_have": [...]}'
    )

    try:
        response = _call_claude(prompt, timeout=45)
        data = _parse_json_response(response)
        result = JDRequirements(
            must_have=[s.strip() for s in data.get("must_have", []) if s.strip()],
            nice_to_have=[s.strip() for s in data.get("nice_to_have", []) if s.strip()],
            source="llm",
        )
    except Exception:
        # Fallback to regex-only if LLM fails
        result = JDRequirements(
            must_have=[],
            nice_to_have=sorted(candidates),
            source="regex",
        )

    if use_cache:
        cache_file.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Keyword coverage scoring.
# ---------------------------------------------------------------------------
@dataclass
class CoverageResult:
    score: float  # 0.0 to 1.0
    matched_must: list[str] = field(default_factory=list)
    missing_must: list[str] = field(default_factory=list)
    matched_nice: list[str] = field(default_factory=list)
    missing_nice: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _keyword_in_text(keyword: str, text_normalized: str) -> bool:
    """Check whether a keyword (or any synonym) appears in normalized text.

    A match on any member of the synonym equivalence class counts. This means
    a CV saying "LLM-powered agents" matches a JD requirement of "LLMs".
    """
    for form in _expand_to_aliases(keyword):
        if not form:
            continue
        pattern = r"(?:^|[^a-z0-9])" + re.escape(form) + r"(?:$|[^a-z0-9])"
        if re.search(pattern, text_normalized):
            return True
    return False


def keyword_coverage(
    cv_text: str,
    requirements: JDRequirements,
) -> CoverageResult:
    """Compute coverage score.

    Scoring formula:
        score = (2 * must_matched_ratio + nice_matched_ratio) / 3

    If there are no must-haves, score = nice_matched_ratio.
    Must-haves are weighted 2x because they're what a human screener checks for
    first in the 7-second scan.
    """
    # Normalize text line-by-line so tokens are lowercased + punctuation stripped,
    # but multi-word phrases are preserved for matching.
    cv_norm = cv_text.lower()
    cv_norm = re.sub(r"[^\w\s./+#-]", " ", cv_norm)
    cv_norm = re.sub(r"\s+", " ", cv_norm).strip()

    matched_must = [m for m in requirements.must_have if _keyword_in_text(m, cv_norm)]
    missing_must = [m for m in requirements.must_have if m not in matched_must]

    matched_nice = [n for n in requirements.nice_to_have if _keyword_in_text(n, cv_norm)]
    missing_nice = [n for n in requirements.nice_to_have if n not in matched_nice]

    must_ratio = len(matched_must) / len(requirements.must_have) if requirements.must_have else 1.0
    nice_ratio = len(matched_nice) / len(requirements.nice_to_have) if requirements.nice_to_have else 1.0

    if requirements.must_have and requirements.nice_to_have:
        score = (2.0 * must_ratio + nice_ratio) / 3.0
    elif requirements.must_have:
        score = must_ratio
    else:
        score = nice_ratio

    return CoverageResult(
        score=round(score, 3),
        matched_must=matched_must,
        missing_must=missing_must,
        matched_nice=matched_nice,
        missing_nice=missing_nice,
    )


# ---------------------------------------------------------------------------
# Keyword stuffing penalty — the one thing human recruiters genuinely penalize.
# ---------------------------------------------------------------------------
# Above this many occurrences of a single JD keyword, a CV reads as stuffing.
_STUFFING_REPEAT_LIMIT = 5
_STUFFING_MAX_PENALTY = 0.10


def keyword_stuffing_penalty(cv_text: str, requirements: JDRequirements) -> float:
    """Penalty in [0, _STUFFING_MAX_PENALTY] for over-repeating JD keywords.

    Tech resumes are screened by humans, not auto-rejected by bots, and recruiters
    are sensitive to keyword stuffing. Subtracting this from the score discourages
    the auto-tailor loop from cramming the same term to inflate coverage — pushing
    it toward the "good enough" band instead of chasing a perfect number.
    """
    cv_norm = cv_text.lower()
    cv_norm = re.sub(r"[^\w\s./+#-]", " ", cv_norm)
    cv_norm = re.sub(r"\s+", " ", cv_norm).strip()

    over = 0
    for kw in list(requirements.must_have) + list(requirements.nice_to_have):
        count = 0
        for form in _expand_to_aliases(kw):
            if not form:
                continue
            # Zero-width boundaries so adjacent repeats ("python python") all count —
            # consuming boundaries would under-count exactly the stuffing we target.
            pattern = r"(?<![a-z0-9])" + re.escape(form) + r"(?![a-z0-9])"
            count += len(re.findall(pattern, cv_norm))
        if count > _STUFFING_REPEAT_LIMIT:
            over += 1
    return min(_STUFFING_MAX_PENALTY, 0.05 * over)


# ---------------------------------------------------------------------------
# PDF parseability — hard gate.
# ---------------------------------------------------------------------------
@dataclass
class PDFParseability:
    parseable: bool
    text_length: int
    found_sections: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)
    has_email: bool = False
    has_phone: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SECTION_HEADERS = {
    "experience": re.compile(r"\b(experience|employment|work\s+history|professional\s+experience)\b", re.IGNORECASE),
    "education": re.compile(r"\beducation\b", re.IGNORECASE),
    "skills": re.compile(r"\b(skills|technical\s+skills|technologies)\b", re.IGNORECASE),
    "summary": re.compile(r"\b(summary|profile|objective|about)\b", re.IGNORECASE),
}

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-().]{7,}\d)")


def check_pdf_parseability(pdf_path: Path) -> PDFParseability:
    """Extract text and confirm the essentials can be recovered."""
    text = extract_pdf_text(pdf_path)

    found = [name for name, rx in _SECTION_HEADERS.items() if rx.search(text)]
    missing = [name for name in _SECTION_HEADERS if name not in found]

    has_email = bool(_EMAIL_RE.search(text))
    has_phone = bool(_PHONE_RE.search(text))

    # Parseable iff: text extractable, contact info present, all major sections found.
    parseable = (
        len(text.strip()) > 300
        and has_email
        and has_phone
        and "experience" in found
        and "education" in found
    )

    return PDFParseability(
        parseable=parseable,
        text_length=len(text),
        found_sections=found,
        missing_sections=missing,
        has_email=has_email,
        has_phone=has_phone,
    )


# ---------------------------------------------------------------------------
# Format audit — known ATS killers.
# ---------------------------------------------------------------------------
@dataclass
class FormatIssue:
    severity: str  # "critical" | "warning" | "info"
    code: str
    message: str


def format_audit(pdf_path: Path) -> list[FormatIssue]:
    """Flag layout features that break common ATS parsers.

    Uses pdfplumber when available for bounding-box analysis; degrades to basic
    pypdf checks otherwise.
    """
    issues: list[FormatIssue] = []

    if not _HAS_PDFPLUMBER:
        # Minimal audit: pypdf page count and image detection
        reader = PdfReader(str(pdf_path))
        if len(reader.pages) > 2:
            issues.append(FormatIssue(
                severity="warning",
                code="page_count",
                message=f"{len(reader.pages)} pages — most ATS truncate CVs to 1-2 pages.",
            ))
        return issues

    with pdfplumber.open(str(pdf_path)) as pdf:
        if len(pdf.pages) > 2:
            issues.append(FormatIssue(
                severity="warning",
                code="page_count",
                message=f"{len(pdf.pages)} pages — most ATS truncate CVs to 1-2 pages.",
            ))

        for page_num, page in enumerate(pdf.pages, start=1):
            # Tables are silent killers — ATS parsers extract cell-by-cell in
            # unpredictable order, scrambling work history.
            tables = page.find_tables()
            if tables:
                issues.append(FormatIssue(
                    severity="critical",
                    code="tables",
                    message=f"Page {page_num} contains {len(tables)} table(s). Tables scramble field order in most ATS.",
                ))

            # Multi-column detection: cluster text-block x-coordinates. If the
            # gap between the two largest clusters is >80pt, it's multi-column.
            chars = page.chars
            if len(chars) > 100:
                xs = sorted({round(c["x0"]) for c in chars})
                # Find the largest gap in x-positions
                gaps = [(xs[i+1] - xs[i], xs[i], xs[i+1]) for i in range(len(xs) - 1)]
                if gaps:
                    max_gap, left, right = max(gaps, key=lambda g: g[0])
                    if max_gap > 120 and left < page.width * 0.6 and right > page.width * 0.4:
                        issues.append(FormatIssue(
                            severity="critical",
                            code="multi_column",
                            message=f"Page {page_num} appears to have a multi-column layout (gap {max_gap:.0f}pt at x={left}). ATS parsers read left-to-right and scramble order.",
                        ))

            # Images suggest text-in-images, which ATS can't OCR reliably.
            images = page.images
            if len(images) > 1:
                issues.append(FormatIssue(
                    severity="warning",
                    code="images",
                    message=f"Page {page_num} has {len(images)} image(s). If any contain text (logos, icons with labels), that text won't be extracted.",
                ))

    return issues


# ---------------------------------------------------------------------------
# Composite score.
# ---------------------------------------------------------------------------
@dataclass
class ATSScore:
    overall: float  # 0.0 to 1.0 — the number the auto-tailor loop optimizes
    coverage: CoverageResult
    parseability: PDFParseability | None
    format_issues: list[FormatIssue]
    threshold_passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "coverage": self.coverage.to_dict(),
            "parseability": self.parseability.to_dict() if self.parseability else None,
            "format_issues": [asdict(i) for i in self.format_issues],
            "threshold_passed": self.threshold_passed,
        }


def ats_score(
    cv_data: dict[str, Any] | None = None,
    jd_text: str = "",
    pdf_path: Path | None = None,
    threshold: float = 0.75,
    use_llm: bool = True,
) -> ATSScore:
    """Composite ATS score.

    Pass ``cv_data`` dict for pre-render scoring (used inside auto_tailor_loop).
    Pass ``pdf_path`` for post-render parseability + format audit. Either or
    both are accepted — at least one must be provided.

    Overall score:
        - Starts as coverage score (0..1)
        - Hard-gated by PDF parseability: if a PDF is provided and not
          parseable, overall is capped at 0.5 regardless of coverage.
        - Critical format issues subtract 0.1 each (max cap).
    """
    if cv_data is None and pdf_path is None:
        raise ValueError("Provide cv_data or pdf_path (or both).")

    if cv_data is not None:
        cv_text = cv_data_to_text(cv_data)
    else:
        cv_text = extract_pdf_text(pdf_path)  # type: ignore[arg-type]

    requirements = extract_jd_requirements(jd_text, use_llm=use_llm) if jd_text else JDRequirements()
    coverage = keyword_coverage(cv_text, requirements)

    parseability: PDFParseability | None = None
    issues: list[FormatIssue] = []
    if pdf_path is not None:
        parseability = check_pdf_parseability(pdf_path)
        issues = format_audit(pdf_path)

    overall = coverage.score
    if parseability is not None and not parseability.parseable:
        overall = min(overall, 0.5)
    critical_count = sum(1 for i in issues if i.severity == "critical")
    overall = max(0.0, overall - 0.1 * critical_count)
    # Recruiters screen tech resumes by hand and penalize keyword stuffing.
    overall = max(0.0, overall - keyword_stuffing_penalty(cv_text, requirements))

    return ATSScore(
        overall=round(overall, 3),
        coverage=coverage,
        parseability=parseability,
        format_issues=issues,
        threshold_passed=overall >= threshold,
    )


# ---------------------------------------------------------------------------
# CLI: python -m jobpilot.ats <cv.json|cv.pdf> <jd.txt>
# ---------------------------------------------------------------------------
def _cli(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: python -m jobpilot.ats <cv.json|cv.pdf> <jd.txt>", file=sys.stderr)
        print("       python -m jobpilot.ats <work_file.json>   # work file with both CV and JD", file=sys.stderr)
        return 2

    cv_arg = Path(argv[0])
    if not cv_arg.exists():
        print(f"Not found: {cv_arg}", file=sys.stderr)
        return 1

    # Mode 1: single arg = a work file containing both cv_data and job
    if len(argv) == 1:
        data = json.loads(cv_arg.read_text(encoding="utf-8"))
        if "cv_data" not in data or "job" not in data:
            print("Work file must have both 'cv_data' and 'job' keys.", file=sys.stderr)
            return 1
        cv_data = data["cv_data"]
        jd_text = data["job"].get("full_description") or data["job"].get("description", "")
        score = ats_score(cv_data=cv_data, jd_text=jd_text, use_llm=True)
    else:
        jd_path = Path(argv[1])
        if not jd_path.exists():
            print(f"Not found: {jd_path}", file=sys.stderr)
            return 1
        jd_text = jd_path.read_text(encoding="utf-8")

        if cv_arg.suffix.lower() == ".pdf":
            score = ats_score(pdf_path=cv_arg, jd_text=jd_text, use_llm=True)
        else:
            cv_data = json.loads(cv_arg.read_text(encoding="utf-8"))
            # Accept either a raw cv_data dict or a work-file wrapper
            if "cv_data" in cv_data:
                cv_data = cv_data["cv_data"]
            score = ats_score(cv_data=cv_data, jd_text=jd_text, use_llm=True)

    print(json.dumps(score.to_dict(), indent=2))
    print(f"\nOverall: {score.overall:.2f}  {'PASS' if score.threshold_passed else 'FAIL'} (threshold 0.75)", file=sys.stderr)
    if score.coverage.missing_must:
        print(f"Missing must-haves ({len(score.coverage.missing_must)}): {score.coverage.missing_must}", file=sys.stderr)
    if score.format_issues:
        print(f"Format issues ({len(score.format_issues)}):", file=sys.stderr)
        for issue in score.format_issues:
            print(f"  [{issue.severity}] {issue.code}: {issue.message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
