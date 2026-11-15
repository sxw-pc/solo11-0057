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


def test_changes_expose_stable_evidence_metadata(events_result):
    changes = events_result["changes"]
    assert changes
    for change in changes:
        assert change["evidence_version"] in {"v1", "v2", "v3"}
        assert change["evidence_direction"] in {"old", "new", "both"}
        assert f".versions.{change['evidence_version']}." in change["evidence_path"]
    by_type = {change["change_type"]: change for change in changes}
    assert by_type["drop_field"]["evidence_direction"] == "old"
    assert by_type["add_field"]["evidence_direction"] == "new"
    assert by_type["rename_field"]["evidence_direction"] == "both"
    assert by_type["field_order_change"]["evidence_direction"] == "both"


def test_markdown_shows_evidence_version_and_direction(events_result):
    rendered = render_markdown_report(events_result)
    assert "Evidence version" in rendered
    assert "Evidence direction" in rendered
    assert "| v1->v2 | drop_field | debug_flag | v1 | old |" in rendered


def test_json_rendering_backfills_missing_evidence_keys():
    rendered = render_json_report({
        "changes": [{
            "change_id": "x", "pair_id": "v1->v2", "from_version": "v1",
            "to_version": "v2", "change_type": "drop_field",
            "field_path": "legacy", "evidence_path": "p", "summary": "s",
            "details": {},
        }]
    })
    payload = json.loads(rendered)
    assert payload["changes"][0]["evidence_version"] == ""
    assert payload["changes"][0]["evidence_direction"] == ""
