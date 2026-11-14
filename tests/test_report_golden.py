from __future__ import annotations

import json
from pathlib import Path

from schemagate.report_html import render_html_report
from schemagate.report_json import render_json_report
from schemagate.report_markdown import render_markdown_report
from schemagate.runner import build_result

GOLDEN = Path(__file__).resolve().parent / "golden"


def _normalized(text: str) -> str:
    return text.replace("\r\n", "\n")


def test_json_report_matches_golden(events_result, events_case):
    rendered = _normalized(render_json_report(events_result))
    expected = _normalized((GOLDEN / "lake_events_report.json").read_text(encoding="utf-8"))
    assert rendered == expected
    assert _normalized(render_json_report(build_result(events_case))) == rendered


def test_markdown_report_matches_golden(events_result):
    rendered = _normalized(render_markdown_report(events_result))
    expected = _normalized((GOLDEN / "lake_events_report.md").read_text(encoding="utf-8"))
    assert rendered == expected
    assert "Consumer compatibility matrix" in rendered
    assert "Migration steps" in rendered


def test_html_report_is_self_contained(events_result):
    rendered = render_html_report(events_result)
    lowered = rendered.lower()
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "cdn" not in lowered
    assert "<style>" in lowered
    assert "<link" not in lowered
    assert "BLOCK" in rendered
    assert "Migration steps" in rendered


def test_html_and_json_outputs_are_stable(events_result):
    assert render_html_report(events_result) == render_html_report(events_result)
    first = render_json_report(events_result)
    second = render_json_report(events_result)
    assert first == second
    assert "generated_at" not in first
    payload = json.loads(first)
    assert payload["report_schema_version"] == 1
    assert payload["decision"] == "BLOCK"
