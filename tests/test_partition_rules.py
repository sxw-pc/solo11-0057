from __future__ import annotations

from schemagate.contracts_parser import parse_partition_spec
from schemagate.partition_rules import diff_partition_specs


def _spec(version, keys, sort=None, file_format="parquet"):
    return parse_partition_spec(
        {
            "table": "demo.unit",
            "version": version,
            "partition_keys": keys,
            "sort_columns": sort or [],
            "file_format": file_format,
        },
        expected_version=version,
        expected_table="demo.unit",
    )


def _diff(old, new):
    return diff_partition_specs("demo.unit", "v1", "v2", old, new)


def test_identical_partitions_have_no_change():
    old = _spec("v1", [{"column": "day", "transform": "identity"}])
    new = _spec("v2", [{"column": "day", "transform": "identity"}])
    assert _diff(old, new) == ()


def test_added_removed_and_reordered_keys():
    old = _spec("v1", [
        {"column": "day", "transform": "identity"},
        {"column": "region", "transform": "identity"},
        {"column": "tenant", "transform": "identity"},
    ])
    new = _spec("v2", [
        {"column": "region", "transform": "identity"},
        {"column": "day", "transform": "identity"},
        {"column": "zone", "transform": "identity"},
    ])
    change = _diff(old, new)[0]
    assert change.change_type == "partition_change"
    assert change.details["added_keys"] == ["zone"]
    assert change.details["removed_keys"] == ["tenant"]
    assert change.details["key_order_changed"] is True
    assert change.evidence_path.endswith("versions.v2.partition_spec")


def test_bucket_count_change():
    old = _spec("v1", [{"column": "region", "transform": "bucket", "bucket_count": 8}])
    new = _spec("v2", [{"column": "region", "transform": "bucket", "bucket_count": 16}])
    details = _diff(old, new)[0].details
    assert details["transform_changes"][0]["column"] == "region"
    assert details["transform_changes"][0]["before"]["bucket_count"] == 8
    assert details["transform_changes"][0]["after"]["bucket_count"] == 16


def test_transform_change_identity_to_truncate():
    old = _spec("v1", [{"column": "code", "transform": "identity"}])
    new = _spec("v2", [{"column": "code", "transform": "truncate", "truncate_width": 4}])
    details = _diff(old, new)[0].details
    assert details["transform_changes"][0]["after"]["transform"] == "truncate"
    assert details["transform_changes"][0]["after"]["truncate_width"] == 4


def test_sort_and_format_changes_are_not_silent():
    old = _spec("v1", [{"column": "day", "transform": "identity"}], sort=["id"], file_format="parquet")
    new = _spec("v2", [{"column": "day", "transform": "identity"}], sort=["day"], file_format="orc")
    details = _diff(old, new)[0].details
    assert details["sort_columns_changed"] is True
    assert details["file_format_changed"] is True


def test_missing_spec_is_treated_as_unpartitioned():
    new = _spec("v2", [{"column": "day", "transform": "identity"}])
    details = _diff(None, new)[0].details
    assert details["added_keys"] == ["day"]


def test_lake_events_partition_evidence_is_present(events_result):
    changes = [item for item in events_result["changes"] if item["change_type"] == "partition_change"]
    assert len(changes) == 2
    assert all(item["evidence_path"].endswith("partition_spec") for item in changes)
    assert events_result["version_pairs"][0]["partition_changed"] is True
