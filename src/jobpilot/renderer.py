from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import jinja2


_TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"

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
    template = _LATEX_ENV.get_template("cv.tex")
    tex_content = template.render(**escaped)
    return _compile_latex(tex_content, output_path)


def render_cover_letter(data: dict[str, Any], output_path: Path) -> Path:
    escaped = _escape_data(data)
    template = _LATEX_ENV.get_template("cover_letter.tex")
    tex_content = template.render(**escaped)
    return _compile_latex(tex_content, output_path)
