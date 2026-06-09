from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import jinja2


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

# Order sections are emitted in when cv_data carries no explicit section_order.
# Follows the book's general guidance: Languages/Technologies high on the page,
# Education lower. Per-variant overrides live in llm.SECTION_ORDER_BY_VARIANT.
DEFAULT_SECTION_ORDER = ["summary", "experience", "skills", "projects", "education", "awards"]

_LATEX_ENV = jinja2.Environment(
    block_start_string=r"\BLOCK{",
    block_end_string="}",
    variable_start_string=r"\VAR{",
    variable_end_string="}",
    comment_start_string=r"\#{",
    comment_end_string="}",
    loader=jinja2.FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=False,
)


def escape_latex(text: str) -> str:
    if not text:
        return ""
    # Use a placeholder for backslash to avoid double-escaping braces
    _BS = "\x00BACKSLASH\x00"
    _TI = "\x00TILDE\x00"
    _CA = "\x00CARET\x00"
    # Step 1: backslash → placeholder (before brace escaping)
    text = text.replace("\\", _BS)
    # Step 2: simple single-char escapes
    text = text.replace("&", r"\&")
    text = text.replace("%", r"\%")
    text = text.replace("$", r"\$")
    text = text.replace("#", r"\#")
    text = text.replace("_", r"\_")
    # Step 3: braces
    text = text.replace("{", r"\{")
    text = text.replace("}", r"\}")
    # Step 4: special chars → placeholders
    text = text.replace("~", _TI)
    text = text.replace("^", _CA)
    # Step 5: replace placeholders with final LaTeX commands
    text = text.replace(_BS, r"\textbackslash{}")
    text = text.replace(_TI, r"\textasciitilde{}")
    text = text.replace(_CA, r"\textasciicircum{}")
    return text


def _escape_data(data: Any) -> Any:
    if isinstance(data, str):
        return escape_latex(data)
    if isinstance(data, list):
        return [_escape_data(item) for item in data]
    if isinstance(data, dict):
        return {k: _escape_data(v) for k, v in data.items()}
    return data


def _check_pdflatex() -> str:
    path = shutil.which("pdflatex")
    if path is None:
        raise RuntimeError(
            "pdflatex not found. Install LaTeX:\n"
            "  macOS: brew install --cask mactex-no-gui\n"
            "  Ubuntu: sudo apt-get install texlive-latex-base\n"
            "  Windows: install MiKTeX from https://miktex.org/"
        )
    return path


def _compile_latex(tex_content: str, output_path: Path) -> Path:
    pdflatex = _check_pdflatex()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_file = Path(tmpdir) / "document.tex"
        tex_file.write_text(tex_content, encoding="utf-8")

        for _ in range(2):  # Run twice for references
            result = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", "-output-directory", tmpdir, str(tex_file)],
                capture_output=True,
                text=True,
                timeout=30,
            )

        pdf_file = Path(tmpdir) / "document.pdf"
        if not pdf_file.exists():
            log_file = Path(tmpdir) / "document.log"
            log_content = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
            raise RuntimeError(
                f"pdflatex compilation failed.\n"
                f"stdout: {result.stdout[-500:]}\n"
                f"stderr: {result.stderr[-500:]}\n"
                f"log (last 1000 chars): {log_content[-1000:]}"
            )

        shutil.copy2(pdf_file, output_path)
    return output_path


def render_cv(data: dict[str, Any], output_path: Path) -> Path:
    escaped = _escape_data(data)
    # Pass raw URL values for \href targets (LaTeX escaping breaks URLs)
    escaped["raw_email"] = data.get("email", "")
    escaped["raw_linkedin"] = data.get("linkedin", "")
    escaped["raw_github"] = data.get("github", "")
    # Fall back to the default layout when the caller didn't specify one.
    if not escaped.get("section_order"):
        escaped["section_order"] = list(DEFAULT_SECTION_ORDER)
    template = _LATEX_ENV.get_template("cv.tex")
    tex_content = template.render(**escaped)
    return _compile_latex(tex_content, output_path)


def render_cover_letter(data: dict[str, Any], output_path: Path) -> Path:
    escaped = _escape_data(data)
    template = _LATEX_ENV.get_template("cover_letter.tex")
    tex_content = template.render(**escaped)
    return _compile_latex(tex_content, output_path)


def pdf_page_info(pdf_path: Path) -> dict[str, Any]:
    """Return page count + per-page text length + last-page fill ratio.

    last_page_fill_ratio = len(text on last page) / max(text lengths of earlier pages).
    Proxy for 'is the last page substantially full?' — used to detect the half-page
    problem (e.g. target 2 pages but only 5 lines on page 2).

    Returns dict with keys: page_count, page_text_lengths, last_page_fill_ratio.
    last_page_fill_ratio is None if the PDF has 0 pages, 1.0 if it has exactly 1 page.
    """
    from pypdf import PdfReader  # local import — pypdf is a test/runtime dep

    reader = PdfReader(str(pdf_path))
    text_lengths = [len(p.extract_text() or "") for p in reader.pages]
    if not text_lengths:
        return {"page_count": 0, "page_text_lengths": [], "last_page_fill_ratio": None}
    if len(text_lengths) == 1:
        return {"page_count": 1, "page_text_lengths": text_lengths, "last_page_fill_ratio": 1.0}
    max_prior = max(text_lengths[:-1]) or 1
    return {
        "page_count": len(text_lengths),
        "page_text_lengths": text_lengths,
        "last_page_fill_ratio": text_lengths[-1] / max_prior,
    }


def check_page_count(
    pdf_path: Path, target_pages: int, fill_threshold: float = 0.5
) -> dict[str, Any]:
    """Check a rendered CV against a target page count.

    Returns dict with: page_count, target_pages, last_page_fill_ratio, meets_target, warning.
    'warning' is None if everything looks fine, else a human-readable description.
    """
    info = pdf_page_info(pdf_path)
    page_count = info["page_count"]
    fill = info["last_page_fill_ratio"]

    if page_count == target_pages and (page_count == 1 or (fill or 0) >= fill_threshold):
        warning: str | None = None
    elif page_count < target_pages:
        warning = (
            f"CV is {page_count} page(s) but target was {target_pages}. "
            f"Consider adding more content (extra bullets, an additional project)."
        )
    elif page_count > target_pages:
        warning = (
            f"CV is {page_count} page(s) but target was {target_pages}. "
            f"Consider trimming bullets or compressing the experience block."
        )
    else:
        # page_count == target_pages but last-page fill is low — the 'half page' case.
        warning = (
            f"CV hits {page_count} pages but the last page is only ~"
            f"{round((fill or 0) * 100)}% full. Consider trimming to {target_pages - 1} "
            f"pages, or adding content to fill the last page."
        )

    return {
        **info,
        "target_pages": target_pages,
        "meets_target": warning is None,
        "warning": warning,
    }
