from __future__ import annotations

import pytest
import yaml

from schemagate.errors import SchemaGateError
from schemagate.loader import CaseLoader, read_yaml
from schemagate.schema_parser import parse_schema_document
from tests.helpers import dump_yaml


def _manifest(case_dir):
    return yaml.safe_load((case_dir / "manifest.yaml").read_text(encoding="utf-8"))


def test_loads_lake_events(events_case):
    assert events_case.case_id == "lake_events"
    assert [item.version for item in events_case.versions] == ["v1", "v2", "v3"]
    assert [item.consumer_id for item in events_case.consumers] == [
        "bi_reader", "etl_writer", "ml_reader"
    ]
    assert events_case.policy.policy_id == "default"
    assert events_case.partitions["v2"].partition_keys[1].transform == "bucket"
    assert events_case.input_fingerprint.startswith("sha256:")


def test_loads_lake_orders(orders_case):
    assert orders_case.table == "demo.lake_orders"
    assert len(orders_case.versions) == 2
    assert orders_case.consumers[0].mode == "backward"
    assert len(orders_case.versions[0].field_index()) == 7


def test_discovery_lists_cases_sorted(loader):
    assert loader.case_ids() == ("lake_events", "lake_orders", "lake_pricing")
    summaries = loader.list_cases()
    assert [item["case_id"] for item in summaries] == [
        "lake_events", "lake_orders", "lake_pricing"
    ]
    assert summaries[0]["schema_count"] == 3


def test_missing_schema_file_has_stable_code(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    (case_dir / "schemas" / "v1.yaml").unlink()
    loader = CaseLoader(fixtures)
    with pytest.raises(SchemaGateError) as caught:
        loader.load_case("lake_orders")
    assert caught.value.code == "SG-LOAD-001"


def test_schema_missing_version_has_stable_code(invalid_dir):
    document = read_yaml(invalid_dir / "missing_version.yaml")
    with pytest.raises(SchemaGateError) as caught:
        parse_schema_document(document)
    assert caught.value.code == "SG-LOAD-008"


def test_bad_type_is_not_silently_stringified(invalid_dir):
    document = read_yaml(invalid_dir / "bad_type.yaml")
    with pytest.raises(SchemaGateError) as caught:
        parse_schema_document(document)
    assert caught.value.code == "SG-LOAD-003"
    assert "made_up_type" in caught.value.details.get("type", "")


def test_duplicate_field_has_stable_code(invalid_dir):
    document = read_yaml(invalid_dir / "duplicate_field.yaml")
    with pytest.raises(SchemaGateError) as caught:
        parse_schema_document(document)
    assert caught.value.code == "SG-LOAD-002"
    assert caught.value.details["path"] == "id"


def test_unknown_mode_is_rejected(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    consumer_path = case_dir / "consumers" / "finance_reader.yaml"
    consumer = yaml.safe_load(consumer_path.read_text(encoding="utf-8"))
    consumer["mode"] = "sideways"
    dump_yaml(consumer_path, consumer)
    loader = CaseLoader(fixtures)
    with pytest.raises(SchemaGateError) as caught:
        loader.load_case("lake_orders")
    assert caught.value.code == "SG-LOAD-005"


def test_empty_consumer_list_is_rejected(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    manifest = _manifest(case_dir)
    manifest["consumers"] = []
    dump_yaml(case_dir / "manifest.yaml", manifest)
    loader = CaseLoader(fixtures)
    with pytest.raises(SchemaGateError) as caught:
        loader.load_case("lake_orders")
    assert caught.value.code == "SG-LOAD-006"


def test_duplicate_version_is_rejected(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    manifest = _manifest(case_dir)
    manifest["schema_versions"].append({"version": "v1", "file": "schemas/v2.yaml"})
    dump_yaml(case_dir / "manifest.yaml", manifest)
    loader = CaseLoader(fixtures)
    with pytest.raises(SchemaGateError) as caught:
        loader.load_case("lake_orders")
    assert caught.value.code == "SG-LOAD-004"


def test_path_escape_is_rejected(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    manifest = _manifest(case_dir)
    manifest["schema_versions"][0]["file"] = "../outside.yaml"
    dump_yaml(case_dir / "manifest.yaml", manifest)
    loader = CaseLoader(fixtures)
    with pytest.raises(SchemaGateError) as caught:
        loader.load_case("lake_orders")
    assert caught.value.code == "SG-LOAD-013"


def test_unknown_case_is_rejected(loader):
    with pytest.raises(SchemaGateError) as caught:
        loader.load_case("missing_case")
    assert caught.value.code == "SG-LOAD-007"
