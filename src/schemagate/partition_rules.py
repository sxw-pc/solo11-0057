"""Declarative partition-spec comparison.

This module never opens data files. It only compares declared partition specs.
When a target :class:`SchemaVersion` is supplied, it also checks every key in
the new spec against that version's field paths so a layout change cannot hide
a key that the target schema no longer supports.
"""

from __future__ import annotations

from typing import Any

from .models import Change, PartitionKey, PartitionSpec, SchemaVersion, TypeSpec
from .naming import change_id, pair_id, partition_evidence
from .type_rules import field_type_signature, type_signature

_INTEGER_KINDS = frozenset({"int32", "int64"})
_STRING_KINDS = frozenset({"string"})
_NUMERIC_KINDS = frozenset({"int32", "int64", "float32", "float64", "decimal"})

# Transforms that constrain the resolved field type. ``identity`` intentionally
# has no entry because it accepts any type.
_TRANSFORM_KINDS: dict[str, frozenset[str]] = {
    "bucket": _INTEGER_KINDS | _STRING_KINDS,
    "truncate": _NUMERIC_KINDS | _STRING_KINDS,
}

_MAX_PATH_DEPTH = 32


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


def _index_entry(
    path: str, type_spec: TypeSpec, signature: str, source: str
) -> dict[str, Any]:
    return {
        "path": path,
        "kind": type_spec.kind,
        "type_signature": signature,
        "source": source,
        "nested": "." in path,
    }


def _add_collection_paths(
    index: dict[str, dict[str, Any]], path: str, type_spec: TypeSpec
) -> None:
    """Expose list/map element paths using the shared type-signature grammar.

    ``SchemaVersion.field_index()`` only materializes declared field paths, so
    collection members are derived here. Their evidence uses
    ``type_rules.type_signature`` rather than a hand-rolled description.
    """
    stack: list[tuple[str, TypeSpec, int]] = [(path, type_spec, 0)]
    while stack:
        base, spec, depth = stack.pop()
        if depth > _MAX_PATH_DEPTH:
            continue
        if spec.kind == "list" and spec.element is not None:
            child = f"{base}.element"
            if child not in index:
                index[child] = _index_entry(
                    child, spec.element, type_signature(spec.element), "list_element"
                )
            stack.append((child, spec.element, depth + 1))
        elif spec.kind == "map":
            if spec.key is not None:
                child = f"{base}.key"
                if child not in index:
                    index[child] = _index_entry(
                        child, spec.key, type_signature(spec.key), "map_key"
                    )
                stack.append((child, spec.key, depth + 1))
            if spec.value is not None:
                child = f"{base}.value"
                if child not in index:
                    index[child] = _index_entry(
                        child, spec.value, type_signature(spec.value), "map_value"
                    )
                stack.append((child, spec.value, depth + 1))


def _schema_path_index(schema: SchemaVersion) -> dict[str, dict[str, Any]]:
    """Build a flat path index over fields, struct children, and collections."""
    index: dict[str, dict[str, Any]] = {}
    for path, field in schema.field_index().items():
        if path not in index:
            index[path] = _index_entry(
                path,
                field.type_spec,
                field_type_signature(field),
                "struct_field" if "." in path else "field",
            )
        _add_collection_paths(index, path, field.type_spec)
    return index


def _resolve_partition_column(
    column: str, index: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    entry = index.get(column)
    if entry is not None:
        return {"status": "resolved", "entry": entry}
    # A bare leaf that only exists below a struct/list/map path is an implicit
    # nested reference; the contract requires the full, explicit path.
    if "." not in column:
        candidates = sorted(
            path for path in index if "." in path and path.rsplit(".", 1)[-1] == column
        )
        if candidates:
            return {"status": "implicit_nested", "candidates": candidates}
    return {"status": "missing", "candidates": []}


def _transform_compatible(transform: str, kind: str) -> bool:
    if transform == "identity":
        return True
    allowed = _TRANSFORM_KINDS.get(transform)
    if allowed is None:
        return False
    return kind in allowed


def _key_evidence(key: PartitionKey, resolution: dict[str, Any]) -> dict[str, Any]:
    entry = resolution.get("entry")
    evidence: dict[str, Any] = {
        "column": key.column,
        "path": key.column,
        "transform": key.transform,
        "bucket_count": key.bucket_count,
        "truncate_width": key.truncate_width,
        "exists": entry is not None,
        "nested": bool(entry and entry["nested"]),
        "source": entry["source"] if entry else None,
        "kind": entry["kind"] if entry else None,
        "type": entry["type_signature"] if entry else None,
        "type_signature": entry["type_signature"] if entry else None,
        "transform_compatible": bool(entry)
        and _transform_compatible(key.transform, entry["kind"]),
        "status": "ok",
        "candidates": [],
    }
    if resolution["status"] == "implicit_nested":
        evidence["candidates"] = list(resolution["candidates"])
        evidence["status"] = "blocked"
        evidence["reason"] = "implicit_nested_path"
        evidence["message"] = (
            f"partition key {key.column} must use an explicit full field path; "
            f"candidate(s): {', '.join(evidence['candidates'])}"
        )
    elif resolution["status"] == "missing":
        evidence["status"] = "blocked"
        evidence["reason"] = "missing_column"
        evidence["message"] = (
            f"partition key {key.column} does not exist in the target schema"
        )
    elif not evidence["transform_compatible"]:
        evidence["status"] = "blocked"
        evidence["reason"] = "transform_incompatible"
        evidence["message"] = (
            f"transform {key.transform} is not compatible with "
            f"{evidence['kind']} at {key.column}"
        )
    return evidence


def _check_partition_consistency(
    keys: tuple[PartitionKey, ...], schema: SchemaVersion
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return evidence for blocked keys and the sorted blocked column names."""
    index = _schema_path_index(schema)
    blocked_evidence: list[dict[str, Any]] = []
    blocked_keys: set[str] = set()
    for key in keys:
        record = _key_evidence(key, _resolve_partition_column(key.column, index))
        if record["status"] != "ok":
            blocked_evidence.append(record)
            blocked_keys.add(key.column)
    return blocked_evidence, sorted(blocked_keys)


def diff_partition_specs(
    table: str,
    old_version: str,
    new_version: str,
    old_spec: PartitionSpec | None,
    new_spec: PartitionSpec | None,
    target_schema: SchemaVersion | None = None,
) -> tuple[Change, ...]:
    """Compare two partition specs, optionally against the target schema.

    ``target_schema`` is the schema declared by ``new_version``. When supplied,
    every key in ``new_spec`` is resolved against that version's field index.
    Evidence for invalid keys is attached as ``consistency_evidence`` and their
    column names as ``blocked_keys``. Those attributes are omitted when all
    keys resolve, so reports for consistent cases are unchanged. An unchanged
    spec still produces a change when a declared key is no longer valid for the
    target schema, because the pair is no longer internally consistent.
    """
    old = _normalize(old_spec)
    new = _normalize(new_spec)
    consistency_evidence: list[dict[str, Any]] = []
    blocked_keys: list[str] = []
    if target_schema is not None:
        consistency_evidence, blocked_keys = _check_partition_consistency(
            tuple(new_spec.partition_keys) if new_spec is not None else (),
            target_schema,
        )
    if old == new and not blocked_keys:
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
    if target_schema is not None and blocked_keys:
        details["consistency_version"] = target_schema.version
        details["consistency_evidence"] = consistency_evidence
        details["blocked_keys"] = blocked_keys
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
    if blocked_keys:
        parts.append("inconsistent keys: " + ",".join(blocked_keys))
    if old == new:
        summary = (
            "partition specification is unchanged but inconsistent with "
            f"schema {new_version}: " + "; ".join(parts)
        )
    else:
        summary = "partition specification changed: " + "; ".join(parts)
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
            summary=summary,
            details=details,
        ),
    )
