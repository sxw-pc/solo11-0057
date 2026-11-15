from __future__ import annotations

import pytest

from schemagate.contracts_parser import parse_partition_spec
from schemagate.models import PartitionKey, PartitionSpec
from schemagate.partition_rules import diff_partition_specs
from tests.helpers import diff_docs, field, schema


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


def _schema(version, fields):
    return schema(version, fields, table="demo.unit")


def _diff(old, new):
    return diff_partition_specs("demo.unit", "v1", "v2", old, new)


def _details(new_keys, target_fields, *, old_keys=()):
    old = _spec("v1", list(old_keys))
    new = _spec("v2", list(new_keys))
    change = diff_partition_specs(
        "demo.unit", "v1", "v2", old, new,
        target_schema=_schema("v2", list(target_fields)),
    )[0]
    return change.details


def _blocked(new_keys, target_fields):
    details = _details(new_keys, target_fields)
    return details.get("blocked_keys", [])


def _evidence(details, column):
    return next(
        item for item in details["consistency_evidence"] if item["column"] == column
    )


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


def test_no_consistency_attributes_without_target_schema():
    old = _spec("v1", [{"column": "day", "transform": "identity"}])
    new = _spec("v2", [{"column": "missing", "transform": "identity"}])
    details = _diff(old, new)[0].details
    assert "blocked_keys" not in details
    assert "consistency_evidence" not in details


def test_consistent_target_schema_adds_no_blocked_keys():
    details = _details(
        [
            {"column": "day", "transform": "identity"},
            {"column": "region", "transform": "bucket", "bucket_count": 8},
        ],
        [
            field("day", "date"),
            field("region", "string", nullable=True),
        ],
    )
    assert "blocked_keys" not in details
    assert "consistency_evidence" not in details


def test_missing_column_is_blocked_with_evidence():
    details = _details(
        [{"column": "region", "transform": "identity"}],
        [field("day", "date")],
    )
    assert details["blocked_keys"] == ["region"]
    assert details["consistency_version"] == "v2"
    evidence = _evidence(details, "region")
    assert evidence["status"] == "blocked"
    assert evidence["reason"] == "missing_column"
    assert evidence["exists"] is False
    assert "does not exist" in evidence["message"]


def test_column_deleted_in_target_version_is_blocked():
    details = _details(
        [{"column": "legacy_code", "transform": "identity"}],
        [field("day", "date")],
        old_keys=[
            {"column": "legacy_code", "transform": "identity"},
            {"column": "day", "transform": "identity"},
        ],
    )
    assert details["removed_keys"] == ["day"]
    assert details["blocked_keys"] == ["legacy_code"]


def test_struct_nested_path_is_resolved_when_explicit():
    details = _details(
        [{"column": "payload.action", "transform": "identity"}],
        [field("payload", "struct", fields=[
            field("action", "string"),
            field("device", "string"),
        ])],
    )
    assert "blocked_keys" not in details


def test_struct_nested_path_must_be_explicit():
    details = _details(
        [{"column": "action", "transform": "identity"}],
        [field("payload", "struct", fields=[field("action", "string")])],
    )
    assert details["blocked_keys"] == ["action"]
    evidence = _evidence(details, "action")
    assert evidence["reason"] == "implicit_nested_path"
    assert evidence["candidates"] == ["payload.action"]


def test_nested_path_that_does_not_exist_is_blocked():
    details = _details(
        [{"column": "payload.unknown", "transform": "identity"}],
        [field("payload", "struct", fields=[field("action", "string")])],
    )
    assert details["blocked_keys"] == ["payload.unknown"]


def test_list_element_path_is_indexed():
    details = _details(
        [{"column": "tags.element", "transform": "truncate", "truncate_width": 4}],
        [field("tags", "list<string>")],
    )
    assert "blocked_keys" not in details


def test_list_element_path_uses_element_type_for_transform_check():
    details = _details(
        [{"column": "tags.element", "transform": "bucket", "bucket_count": 4}],
        [field("tags", "list<string>")],
    )
    assert "blocked_keys" not in details

    incompatible = _details(
        [{"column": "tags.element", "transform": "bucket", "bucket_count": 4}],
        [field("tags", "list<boolean>")],
    )
    assert incompatible["blocked_keys"] == ["tags.element"]


def test_map_key_and_value_paths_are_indexed():
    details = _details(
        [
            {"column": "attrs.key", "transform": "bucket", "bucket_count": 4},
            {"column": "attrs.value", "transform": "truncate", "truncate_width": 2},
        ],
        [field("attrs", "map<string,int64>")],
    )
    assert "blocked_keys" not in details


def test_nested_list_element_signature_is_recorded_in_evidence():
    details = _details(
        [{"column": "matrix.element", "transform": "identity"}],
        [field("matrix", "list<list<int64>>")],
    )
    assert "blocked_keys" not in details
    # A deeper path is indexed from the shared type-signature grammar.
    deeper = _details(
        [{"column": "matrix.element.element", "transform": "bucket", "bucket_count": 2}],
        [field("matrix", "list<list<int64>>")],
    )
    assert "blocked_keys" not in deeper


@pytest.mark.parametrize(
    ("transform", "params", "type_name", "allowed"),
    [
        ("bucket", {"bucket_count": 4}, "int32", True),
        ("bucket", {"bucket_count": 4}, "int64", True),
        ("bucket", {"bucket_count": 4}, "string", True),
        ("bucket", {"bucket_count": 4}, "boolean", False),
        ("bucket", {"bucket_count": 4}, "date", False),
        ("bucket", {"bucket_count": 4}, "float64", False),
        ("truncate", {"truncate_width": 4}, "string", True),
        ("truncate", {"truncate_width": 4}, "int32", True),
        ("truncate", {"truncate_width": 4}, "float64", True),
        ("truncate", {"truncate_width": 4}, "decimal(10,2)", True),
        ("truncate", {"truncate_width": 4}, "boolean", False),
        ("truncate", {"truncate_width": 4}, "date", False),
        ("identity", {}, "boolean", True),
        ("identity", {}, "date", True),
        ("identity", {}, "struct", True),
    ],
)
def test_transform_type_compatibility(transform, params, type_name, allowed):
    key = {"column": "value", "transform": transform, **params}
    if type_name == "struct":
        target = [field("value", "struct", fields=[field("inner", "string")])]
    else:
        target = [field("value", type_name)]
    details = _details([key], target)
    assert ("blocked_keys" not in details) is allowed
    if not allowed:
        evidence = _evidence(details, "value")
        assert evidence["reason"] == "transform_incompatible"
        assert evidence["transform_compatible"] is False
        assert evidence["kind"] == type_name.split("(")[0]


def test_added_nullable_or_defaulted_key_on_target_is_allowed():
    details = _details(
        [
            {"column": "day", "transform": "identity"},
            {"column": "region", "transform": "identity"},
            {"column": "ingest_source", "transform": "identity"},
        ],
        [
            field("day", "date"),
            field("region", "string"),
            field("ingest_source", "string", nullable=False, default="batch"),
        ],
        old_keys=[{"column": "day", "transform": "identity"}],
    )
    assert details["added_keys"] == ["ingest_source", "region"]
    assert "blocked_keys" not in details


def test_diff_engine_passes_the_target_schema():
    changes = diff_docs(
        {"table": "demo.unit", "version": "v1", "fields": [field("id", "string")]},
        {"table": "demo.unit", "version": "v2", "fields": [field("id", "string")]},
        old_partition=_spec("v1", []),
        new_partition=_spec("v2", [{"column": "gone", "transform": "identity"}]),
    )
    partition = [item for item in changes if item.change_type == "partition_change"]
    assert partition[0].details["blocked_keys"] == ["gone"]


def test_missing_target_partition_spec_keys_are_skipped():
    # Only keys declared by the new spec are validated. An old key that is
    # dropped from the spec is reported as removed_keys, not as missing.
    old = _spec("v1", [{"column": "legacy", "transform": "identity"}])
    new = _spec("v2", [{"column": "day", "transform": "identity"}])
    change = diff_partition_specs(
        "demo.unit", "v1", "v2", old, new,
        target_schema=_schema("v2", [field("day", "date")]),
    )[0]
    assert change.details["removed_keys"] == ["legacy"]
    assert "blocked_keys" not in change.details


def test_existing_legacy_call_signature_still_works():
    old = _spec("v1", [{"column": "day", "transform": "identity"}])
    new = _spec("v2", [{"column": "day", "transform": "identity"},
                       {"column": "region", "transform": "identity"}])
    change = diff_partition_specs("demo.unit", "v1", "v2", old, new, None)[0]
    assert change.details["added_keys"] == ["region"]
    assert "blocked_keys" not in change.details


def test_spec_objects_can_be_built_without_the_parser():
    old = PartitionSpec("v1", "demo.unit", (), (), "parquet")
    new = PartitionSpec(
        "v2", "demo.unit",
        (PartitionKey("day", "identity"),),
        (), "parquet",
    )
    change = diff_partition_specs(
        "demo.unit", "v1", "v2", old, new,
        target_schema=_schema("v2", [field("day", "date")]),
    )[0]
    assert change.change_type == "partition_change"
    assert "blocked_keys" not in change.details


def test_unchanged_spec_is_still_checked_against_the_target_schema():
    old = _spec("v1", [{"column": "legacy_code", "transform": "identity"}])
    new = _spec("v2", [{"column": "legacy_code", "transform": "identity"}])
    change = diff_partition_specs(
        "demo.unit", "v1", "v2", old, new,
        target_schema=_schema("v2", [field("day", "date")]),
    )[0]
    assert change.change_type == "partition_change"
    assert change.details["blocked_keys"] == ["legacy_code"]
    assert "inconsistent keys: legacy_code" in change.summary


def test_unchanged_and_consistent_spec_still_produces_no_change():
    old = _spec("v1", [{"column": "day", "transform": "identity"}])
    new = _spec("v2", [{"column": "day", "transform": "identity"}])
    assert diff_partition_specs(
        "demo.unit", "v1", "v2", old, new,
        target_schema=_schema("v2", [field("day", "date")]),
    ) == ()


def test_element_path_evidence_records_simplified_type_signature():
    details = _details(
        [{"column": "attrs.value", "transform": "bucket", "bucket_count": 4}],
        [field("attrs", "map<string,list<int64>>")],
    )
    evidence = _evidence(details, "attrs.value")
    assert evidence["reason"] == "transform_incompatible"
    assert evidence["source"] == "map_value"
    assert evidence["kind"] == "list"
    assert evidence["type_signature"] == "list<int64>"
    assert evidence["type"] == "list<int64>"


def test_nested_list_element_evidence_uses_element_signature():
    details = _details(
        [{"column": "matrix.element.element", "transform": "bucket", "bucket_count": 2}],
        [field("matrix", "list<list<boolean>>")],
    )
    evidence = _evidence(details, "matrix.element.element")
    assert evidence["kind"] == "boolean"
    assert evidence["type_signature"] == "boolean"
    assert evidence["nested"] is True


def test_map_key_evidence_records_key_signature():
    details = _details(
        [{"column": "attrs.key", "transform": "truncate", "truncate_width": 2}],
        [field("attrs", "map<boolean,int64>")],
    )
    evidence = _evidence(details, "attrs.key")
    assert evidence["source"] == "map_key"
    assert evidence["kind"] == "boolean"
