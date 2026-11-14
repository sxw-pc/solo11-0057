"""Declarative partition-spec comparison.

This module never opens data files. It only compares declared partition specs.
"""

from __future__ import annotations

from typing import Any

from .models import Change, PartitionSpec
from .naming import change_id, pair_id, partition_evidence


def _normalize(spec: PartitionSpec | None) -> dict[str, Any]:
    if spec is None:
        return {"keys": [], "sort_columns": [], "file_format": ""}
    return {
        "keys": [
            {
                "column": key.column,
                "transform": key.transform,
                "bucket_count": key.bucket_count,
                "truncate_width": key.truncate_width,
            }
            for key in spec.partition_keys
        ],
        "sort_columns": list(spec.sort_columns),
        "file_format": spec.file_format,
    }


def diff_partition_specs(
    table: str,
    old_version: str,
    new_version: str,
    old_spec: PartitionSpec | None,
    new_spec: PartitionSpec | None,
) -> tuple[Change, ...]:
    old = _normalize(old_spec)
    new = _normalize(new_spec)
    if old == new:
        return ()
    old_keys = {item["column"]: item for item in old["keys"]}
    new_keys = {item["column"]: item for item in new["keys"]}
    old_columns = [item["column"] for item in old["keys"]]
    new_columns = [item["column"] for item in new["keys"]]
    common = [column for column in old_columns if column in new_keys]
    new_common_order = [column for column in new_columns if column in old_keys]
    transform_changes = []
    for column in common:
        before, after = old_keys[column], new_keys[column]
        if before != after:
            transform_changes.append({"column": column, "before": before, "after": after})
    details = {
        "added_keys": sorted(set(new_columns) - set(old_columns)),
        "removed_keys": sorted(set(old_columns) - set(new_columns)),
        "key_order_changed": common != new_common_order,
        "old_key_order": old_columns,
        "new_key_order": new_columns,
        "transform_changes": transform_changes,
        "sort_columns_changed": old["sort_columns"] != new["sort_columns"],
        "old_sort_columns": old["sort_columns"],
        "new_sort_columns": new["sort_columns"],
        "file_format_changed": old["file_format"] != new["file_format"],
        "old_file_format": old["file_format"],
        "new_file_format": new["file_format"],
    }
    parts = []
    if details["added_keys"]:
        parts.append("added keys: " + ",".join(details["added_keys"]))
    if details["removed_keys"]:
        parts.append("removed keys: " + ",".join(details["removed_keys"]))
    if details["key_order_changed"]:
        parts.append("key order changed")
    if transform_changes:
        parts.append(f"{len(transform_changes)} transform change(s)")
    if details["sort_columns_changed"]:
        parts.append("sort columns changed")
    if details["file_format_changed"]:
        parts.append("file format changed")
    pair = pair_id(old_version, new_version)
    return (
        Change(
            change_id=change_id(table, pair, "$partition_spec", "partition_change"),
            pair_id=pair,
            from_version=old_version,
            to_version=new_version,
            change_type="partition_change",
            field_path="$partition_spec",
            evidence_path=partition_evidence(table, new_version),
            summary="partition specification changed: " + "; ".join(parts),
            details=details,
        ),
    )
