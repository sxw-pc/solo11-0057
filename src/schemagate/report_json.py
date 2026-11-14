"""Stable JSON report rendering."""

from __future__ import annotations

from typing import Any

from .canonical import stable_json


def render_json_report(result: dict[str, Any]) -> str:
    """Render the canonical report with sorted keys and a trailing newline."""
    return stable_json(result, indent=2)
