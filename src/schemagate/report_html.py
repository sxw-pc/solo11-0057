"""Self-contained HTML report rendering with local-only styling."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

_PACKAGE = Path(__file__).resolve().parent
_ENVIRONMENT = Environment(
    loader=FileSystemLoader(str(_PACKAGE / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _css() -> str:
    css_path = _PACKAGE / "static" / "report.css"
    try:
        return css_path.read_text(encoding="utf-8")
    except OSError:
        return "body{font-family:sans-serif;margin:2rem;}"


def render_html_report(result: dict[str, Any]) -> str:
    template = _ENVIRONMENT.get_template("report.html")
    rendered = template.render(result=result, css=_css())
    return rendered.rstrip("\n") + "\n"
