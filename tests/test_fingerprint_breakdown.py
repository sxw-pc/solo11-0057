"""Hierarchical fingerprint breakdown and ledger reverse-validation tests."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from schemagate.canonical import (
    affected_pair_ids,
    fingerprint_breakdown,
    input_fingerprint,
)
from schemagate.app import create_app
from schemagate.errors import SchemaGateError
from schemagate.loader import CaseLoader
from schemagate.runner import build_result, compute_run_id
from tests.helpers import dump_yaml

CASES = ("schema", "partition", "consumer", "policy")


def _breakdown(case):
    return fingerprint_breakdown(case)


def _domain(before: dict, after: dict, name: str) -> tuple[str, str]:
    return before["domains"][name], after["domains"][name]


def _pair_map(breakdown: dict) -> dict[str, str]:
    return {entry["pair_id"]: entry["fingerprint"] for entry in breakdown["version_pairs"]}


def _consumer_map(breakdown: dict) -> dict[str, dict]:
    return {entry["consumer_id"]: entry for entry in breakdown["consumers"]}


# ---------------------------------------------------------------------------
# Structure and determinism
# ---------------------------------------------------------------------------


def test_breakdown_covers_all_domains_pairs_and_consumers(events_case):
    breakdown = _breakdown(events_case)
    assert breakdown["input_fingerprint"] == events_case.input_fingerprint
    assert set(breakdown["domains"]) == set(CASES)
    assert [entry["pair_id"] for entry in breakdown["version_pairs"]] == [
        "v1->v2", "v2->v3"
    ]
    for entry in breakdown["version_pairs"]:
        assert set(entry) == {"pair_id", "from_version", "to_version", "fingerprint"}
        assert entry["fingerprint"].startswith("sha256:")
    assert [entry["consumer_id"] for entry in breakdown["consumers"]] == [
        "bi_reader", "etl_writer", "ml_reader"
    ]
    for entry in breakdown["consumers"]:
        assert set(entry) == {
            "consumer_id", "fingerprint", "anchor_version", "affected_pair_ids"
        }
        assert entry["fingerprint"].startswith("sha256:")


def test_breakdown_is_deterministic(events_case):
    first = _breakdown(events_case)
    second = _breakdown(events_case)
    assert first == second
    # Reloading from disk yields the same decomposition.
    reloaded = CaseLoader("fixtures/cases").load_case("lake_events")
    assert _breakdown(reloaded) == first


def test_run_id_still_derived_from_full_fingerprint_only(events_case):
    result = build_result(events_case)
    assert result["run_id"] == compute_run_id(events_case)
    assert result["input_fingerprint"] == events_case.input_fingerprint
    assert result["fingerprint_breakdown"]["input_fingerprint"] == (
        events_case.input_fingerprint
    )


# ---------------------------------------------------------------------------
# affected pair semantics mirror compatibility.affected_changes
# ---------------------------------------------------------------------------


def test_affected_pair_ids_match_anchor_semantics(events_case):
    positions = {version.version: index for index, version in enumerate(events_case.versions)}
    breakdown = _breakdown(events_case)
    for consumer in events_case.consumers:
        anchor = max(positions[v] for v in consumer.declared_versions)
        expected = [
            f"{events_case.versions[i].version}->{events_case.versions[i + 1].version}"
            for i in range(len(events_case.versions) - 1)
            if i >= anchor
        ]
        assert affected_pair_ids(events_case, consumer) == expected
        entry = _consumer_map(breakdown)[consumer.consumer_id]
        assert entry["affected_pair_ids"] == expected
        assert entry["anchor_version"] == events_case.versions[anchor].version
    # ml_reader declares v1 and v2, so its anchor is v2: only v2->v3 affects it.
    assert _consumer_map(breakdown)["ml_reader"]["affected_pair_ids"] == ["v2->v3"]


def test_affected_pair_ids_agree_with_evaluated_findings(events_case):
    result = build_result(events_case)
    breakdown_pairs = {
        entry["consumer_id"]: set(entry["affected_pair_ids"])
        for entry in result["fingerprint_breakdown"]["consumers"]
    }
    for verdict in result["consumers"]:
        finding_pairs = {
            finding["pair_id"]
            for finding in verdict["findings"]
            if finding["pair_id"]
        }
        assert finding_pairs <= breakdown_pairs[verdict["consumer_id"]]


# ---------------------------------------------------------------------------
# Domain isolation on the lake_events fixture
# ---------------------------------------------------------------------------


def test_consumer_mode_change_moves_only_that_consumer(copied_case):
    """The lake_events reproduction: flipping ml_reader's mode.

    Pair sub-fingerprints cover only the adjacent schema versions and the
    partition specs on that pair, so a consumer declaration change must not
    move any pair fingerprint; the set of pairs affecting each consumer is
    anchor-driven and likewise stays stable.
    """
    fixtures, case_dir = copied_case("lake_events")
    before_case = CaseLoader(fixtures).load_case("lake_events")
    before = _breakdown(before_case)

    consumer_path = case_dir / "consumers" / "ml_reader.yaml"
    document = yaml.safe_load(consumer_path.read_text(encoding="utf-8"))
    original_mode = document["mode"]
    document["mode"] = "backward" if original_mode == "full" else "full"
    dump_yaml(consumer_path, document)

    after_case = CaseLoader(fixtures).load_case("lake_events")
    after = _breakdown(after_case)

    # Full fingerprint changes, but identity remains a pure function of it.
    assert input_fingerprint(after_case) != input_fingerprint(before_case)
    assert compute_run_id(after_case) != compute_run_id(before_case)

    # Only the consumer domain moves; schema, partition and policy are stable.
    assert _domain(before, after, "consumer")[0] != _domain(before, after, "consumer")[1]
    for domain in ("schema", "partition", "policy"):
        assert _domain(before, after, domain)[0] == _domain(before, after, domain)[1]

    # Only ml_reader's per-consumer fingerprint changes.
    before_consumers, after_consumers = _consumer_map(before), _consumer_map(after)
    assert before_consumers["ml_reader"]["fingerprint"] != (
        after_consumers["ml_reader"]["fingerprint"]
    )
    for other in ("bi_reader", "etl_writer"):
        assert before_consumers[other] == after_consumers[other]

    # Pair fingerprints and every consumer's affected-pair set are unchanged.
    assert _pair_map(before) == _pair_map(after)
    for consumer_id in before_consumers:
        assert before_consumers[consumer_id]["affected_pair_ids"] == (
            after_consumers[consumer_id]["affected_pair_ids"]
        )
    # ml_reader still only sees the pair starting at its anchor (v2->v3).
    assert after_consumers["ml_reader"]["affected_pair_ids"] == ["v2->v3"]


def test_schema_change_moves_schema_domain_and_touching_pairs_only(copied_case):
    fixtures, case_dir = copied_case("lake_events")
    before = _breakdown(CaseLoader(fixtures).load_case("lake_events"))

    schema_path = case_dir / "schemas" / "v3.yaml"
    document = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    document["fields"].append({
        "name": "experimental_score",
        "type": "double",
        "nullable": True,
        "field_id": 99,
    })
    dump_yaml(schema_path, document)

    after = _breakdown(CaseLoader(fixtures).load_case("lake_events"))

    assert _domain(before, after, "schema")[0] != _domain(before, after, "schema")[1]
    for domain in ("partition", "consumer", "policy"):
        assert _domain(before, after, domain)[0] == _domain(before, after, domain)[1]

    before_pairs, after_pairs = _pair_map(before), _pair_map(after)
    assert before_pairs["v1->v2"] == after_pairs["v1->v2"]
    assert before_pairs["v2->v3"] != after_pairs["v2->v3"]

    # Per-consumer declaration fingerprints are independent of schemas.
    assert _consumer_map(before) == _consumer_map(after)


def test_partition_change_moves_partition_domain_and_touching_pairs_only(
    copied_case,
):
    fixtures, case_dir = copied_case("lake_events")
    before = _breakdown(CaseLoader(fixtures).load_case("lake_events"))

    partition_path = case_dir / "partitions" / "v2.yaml"
    document = yaml.safe_load(partition_path.read_text(encoding="utf-8"))
    document["file_format"] = "orc"
    dump_yaml(partition_path, document)

    after = _breakdown(CaseLoader(fixtures).load_case("lake_events"))

    assert _domain(before, after, "partition")[0] != (
        _domain(before, after, "partition")[1]
    )
    for domain in ("schema", "consumer", "policy"):
        assert _domain(before, after, domain)[0] == _domain(before, after, domain)[1]

    before_pairs, after_pairs = _pair_map(before), _pair_map(after)
    # v2 sits on the "to" side of v1->v2 and the "from" side of v2->v3.
    assert before_pairs["v1->v2"] != after_pairs["v1->v2"]
    assert before_pairs["v2->v3"] != after_pairs["v2->v3"]
    assert _consumer_map(before) == _consumer_map(after)


def test_policy_change_moves_only_policy_domain(copied_case):
    fixtures, case_dir = copied_case("lake_events")
    before = _breakdown(CaseLoader(fixtures).load_case("lake_events"))

    policy_path = case_dir / "policies" / "default.yaml"
    document = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    document["forbidden_changes"] = ["drop_field"]
    dump_yaml(policy_path, document)

    after = _breakdown(CaseLoader(fixtures).load_case("lake_events"))

    assert _domain(before, after, "policy")[0] != _domain(before, after, "policy")[1]
    for domain in ("schema", "partition", "consumer"):
        assert _domain(before, after, domain)[0] == _domain(before, after, domain)[1]
    assert _pair_map(before) == _pair_map(after)
    assert _consumer_map(before) == _consumer_map(after)


def test_each_consumer_declaration_field_moves_its_own_fingerprint(events_case):
    """mode, read/write versions, widening, partition allow, severity override."""
    base = _consumer_map(_breakdown(events_case))["ml_reader"]["fingerprint"]
    original = next(c for c in events_case.consumers if c.consumer_id == "ml_reader")
    mutations = [
        replace(original, mode="backward"),
        replace(original, read_versions=("v1", "v2", "v3")),
        replace(original, write_versions=("v3",)),
        replace(original, allowed_type_widening=("int32->int64", "float32->float64")),
        replace(original, allow_partition_change=False),
        replace(original, severity_overrides={"timestamp_unit_change": "BLOCK"}),
    ]
    for mutated in mutations:
        case = replace(
            events_case,
            consumers=tuple(
                mutated if c.consumer_id == "ml_reader" else c
                for c in events_case.consumers
            ),
        )
        entry = _consumer_map(_breakdown(case))["ml_reader"]
        assert entry["fingerprint"] != base
        # Other consumers and the pair surface stay untouched.
        others = _consumer_map(_breakdown(case))
        for other in ("bi_reader", "etl_writer"):
            assert others[other] == _consumer_map(_breakdown(events_case))[other]


def test_pair_fingerprint_reflects_partition_spec_and_both_schemas(events_case):
    before = _pair_map(_breakdown(events_case))
    v1, v2, v3 = events_case.versions
    original_v2_partition = events_case.partitions["v2"]

    # A partition-only change on v2 moves both adjacent pair fingerprints.
    partition_case = replace(
        events_case,
        partitions={**events_case.partitions, "v2": replace(
            original_v2_partition, file_format="avro"
        )},
    )
    after_partition = _pair_map(_breakdown(partition_case))
    assert after_partition["v1->v2"] != before["v1->v2"]
    assert after_partition["v2->v3"] != before["v2->v3"]

    # A schema-only change on v3 moves only the pair ending at v3.
    schema_case = replace(
        events_case,
        versions=(v1, v2, replace(v3, fields=v3.fields[:-1])),
    )
    after_schema = _pair_map(_breakdown(schema_case))
    assert after_schema["v1->v2"] == before["v1->v2"]
    assert after_schema["v2->v3"] != before["v2->v3"]


# ---------------------------------------------------------------------------
# Ledger reuse and SG-LEDGER-004 reverse validation
# ---------------------------------------------------------------------------


def _service(app):
    return app.extensions["schemagate"]


def test_repeated_run_reuses_with_same_run_id_and_marker(app):
    service = _service(app)
    first = service.execute("lake_events")
    second = service.execute("lake_events")
    assert first.run_id == second.run_id
    assert first.reused is False
    assert second.reused is True
    assert second.attempt_count == 2
    stored = service.ledger.get_run(first.run_id)
    assert stored["fingerprint_breakdown"] == (
        first.result["fingerprint_breakdown"]
    )


def test_breakdown_mismatch_under_same_fingerprint_raises_sg_ledger_004(app):
    service = _service(app)
    outcome = service.execute("lake_events")
    breakdown = outcome.result["fingerprint_breakdown"]

    # Simulate a canonicalization blind spot: the stored full fingerprint and
    # result bytes stay identical while the recorded decomposition differs.
    tampered = json.loads(json.dumps(breakdown))
    tampered["domains"]["schema"] = "sha256:" + "0" * 64
    with sqlite3.connect(service.ledger.db_path) as connection:
        connection.execute(
            "UPDATE runs SET fingerprint_breakdown_json = ? WHERE run_id = ?",
            (json.dumps(tampered, sort_keys=True, separators=(",", ":")),
             outcome.run_id),
        )
        connection.commit()

    with pytest.raises(SchemaGateError) as caught:
        service.execute("lake_events")
    assert caught.value.code == "SG-LEDGER-004"
    assert caught.value.details["run_id"] == outcome.run_id
    assert "domains" in caught.value.details["mismatched_sections"]

    # The failed reuse must not increment the attempt counter.
    metadata = service.ledger.get_run_metadata(outcome.run_id)
    assert metadata["attempt_count"] == 1


def test_breakdown_column_survives_ledger_reopen(tmp_path, app):
    service = _service(app)
    outcome = service.execute("lake_events")
    from schemagate.ledger import Ledger

    reopened = Ledger(service.ledger.db_path)
    metadata = reopened.get_run_metadata(outcome.run_id)
    assert metadata is not None
    row = reopened.get_run(outcome.run_id)
    assert row["fingerprint_breakdown"] == (
        outcome.result["fingerprint_breakdown"]
    )


def test_legacy_ledger_without_breakdown_column_is_migrated(tmp_path):
    """A pre-breakdown runs table gains the column lazily and stays usable."""
    from schemagate.ledger import Ledger

    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE cases (
                case_id TEXT PRIMARY KEY, table_name TEXT NOT NULL,
                description TEXT NOT NULL, first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, case_id TEXT NOT NULL,
                table_name TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
                tool_version TEXT NOT NULL, rules_version TEXT NOT NULL,
                report_schema_version INTEGER NOT NULL, decision TEXT NOT NULL,
                summary_json TEXT NOT NULL, result_json TEXT NOT NULL,
                report_json_path TEXT, report_markdown_path TEXT,
                report_html_path TEXT, attempt_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
            );
            """
        )
        connection.commit()

    # Construction must add the missing column instead of failing.
    ledger = Ledger(db_path)
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    assert "fingerprint_breakdown_json" in columns

    # A fresh run then records and reuses with the breakdown check active.
    fixtures_dir = Path(__file__).resolve().parents[1] / "fixtures" / "cases"
    app = create_app(
        fixtures_dir=fixtures_dir,
        db_path=db_path,
        reports_dir=tmp_path / "reports",
        testing=True,
    )
    service = app.extensions["schemagate"]
    first = service.execute("lake_events")
    second = service.execute("lake_events")
    assert second.reused is True
    assert first.run_id == second.run_id
    assert service.ledger.get_run(first.run_id)["fingerprint_breakdown"] == (
        first.result["fingerprint_breakdown"]
    )
