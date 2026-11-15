"""Stable JSON report rendering."""

from __future__ import annotations

from typing import Any

from .canonical import stable_json

_EVIDENCE_FIELDS = ("evidence_version", "evidence_direction")


def _stable_evidence(change: dict[str, Any]) -> dict[str, Any]:
    """Guarantee both evidence keys exist for every rendered change."""
    payload = dict(change)
    for name in _EVIDENCE_FIELDS:
        payload[name] = payload.get(name) or ""
    return payload


def render_json_report(result: dict[str, Any]) -> str:
    """Render the canonical report with sorted keys and a trailing newline."""
    projected = dict(result)
    changes = result.get("changes")
    if isinstance(changes, list):
        projected["changes"] = [_stable_evidence(change) for change in changes]
    return stable_json(projected, indent=2)
