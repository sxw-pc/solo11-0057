from __future__ import annotations

import json

import pytest

from schemagate.errors import SchemaGateError


def _service(app):
    return app.extensions["schemagate"]


def test_repeated_runs_are_idempotent(app):
    service = _service(app)
    first = service.execute("lake_events")
    second = service.execute("lake_events")
    assert first.run_id == second.run_id
    assert first.decision == second.decision
    assert first.attempt_count == 1
    assert second.attempt_count == 2
    assert second.reused is True
    records = service.ledger.list_runs()
    assert len(records) == 1
    assert records[0]["attempt_count"] == 2


def test_report_files_are_written(app):
    service = _service(app)
    outcome = service.execute("lake_events")
    metadata = service.ledger.get_run_metadata(outcome.run_id)
    assert metadata is not None
    for path in metadata["report_paths"].values():
        assert path and path.endswith((".json", ".md", ".html"))
    with open(metadata["report_paths"]["json"], encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["decision"] == "BLOCK"


def test_case_filter_and_cross_case_isolation(app):
    service = _service(app)
    events = service.execute("lake_events")
    orders = service.execute("lake_orders")
    assert events.run_id != orders.run_id
    events_rows = service.ledger.list_runs(case_id="lake_events")
    orders_rows = service.ledger.list_runs(case_id="lake_orders")
    assert [row["case_id"] for row in events_rows] == ["lake_events"]
    assert [row["case_id"] for row in orders_rows] == ["lake_orders"]
    assert service.ledger.get_run(events.run_id)["case"]["case_id"] == "lake_events"
    assert service.ledger.get_run(orders.run_id)["case"]["case_id"] == "lake_orders"


def test_sql_injection_filter_is_parameterized(app):
    service = _service(app)
    service.execute("lake_events")
    rows = service.ledger.list_runs(case_id="lake_events' OR 1=1 --")
    assert rows == []
    assert len(service.ledger.list_runs()) == 1


def test_invalid_limit_is_rejected(app):
    with pytest.raises(SchemaGateError) as caught:
        _service(app).ledger.list_runs(limit=0)
    assert caught.value.code == "SG-LEDGER-003"


def test_conflicting_result_for_same_fingerprint_is_rejected(app):
    service = _service(app)
    outcome = service.execute("lake_events")
    conflicting = dict(outcome.result)
    conflicting["decision"] = "REVIEW"
    with pytest.raises(SchemaGateError) as caught:
        service.ledger.register_run(conflicting, {})
    assert caught.value.code == "SG-LEDGER-001"


def test_second_run_reuses_identical_report_bytes(app):
    service = _service(app)
    outcome = service.execute("lake_events")
    path = service.ledger.get_run_metadata(outcome.run_id)["report_paths"]["markdown"]
    with open(path, "rb") as handle:
        before = handle.read()
    service.execute("lake_events")
    with open(path, "rb") as handle:
        after = handle.read()
    assert before == after
