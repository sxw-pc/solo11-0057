"""Parsers for policy, consumer, and partition specification documents."""

from __future__ import annotations

import re
from typing import Any

from .config import CHANGE_TYPES, COMPAT_MODES, PARTITION_TRANSFORMS, SEVERITIES
from .errors import SchemaGateError
from .models import ConsumerContract, PartitionKey, PartitionSpec, Policy
from .type_rules import normalize_widening

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _error(code: str, message: str, **details: Any) -> SchemaGateError:
    return SchemaGateError(code, message, details)


def _text(value: Any, label: str, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(code, f"{label} must be a non-empty string")
    return value.strip()


def _string_list(value: Any, label: str, code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _error(code, f"{label} must be a list of strings")
    result: list[str] = []
    seen = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _error(code, f"{label} must contain non-empty strings")
        normalized = item.strip()
        if normalized in seen:
            raise _error(code, f"{label} contains a duplicate entry", entry=normalized)
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def parse_policy(document: Any) -> Policy:
    if not isinstance(document, dict):
        raise _error("SG-LOAD-011", "policy document must be a mapping")
    allowed = {"policy_id", "default_mode", "forbidden_changes", "review_changes", "description"}
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise _error("SG-LOAD-011", "unknown policy attributes", attributes=unknown)
    policy_id = document.get("policy_id", "default")
    if not isinstance(policy_id, str) or not _ID_RE.match(policy_id):
        raise _error("SG-LOAD-011", "policy_id is not a valid identifier")
    mode = document.get("default_mode", "backward")
    if mode not in COMPAT_MODES:
        raise _error("SG-LOAD-005", "unknown compatibility mode", mode=mode)
    forbidden = _string_list(document.get("forbidden_changes"), "forbidden_changes", "SG-LOAD-011")
    review = _string_list(document.get("review_changes"), "review_changes", "SG-LOAD-011")
    for change_type in forbidden + review:
        if change_type not in CHANGE_TYPES:
            raise _error("SG-LOAD-011", "unknown change type in policy", change_type=change_type)
    overlap = sorted(set(forbidden) & set(review))
    if overlap:
        raise _error("SG-LOAD-011", "changes cannot be both forbidden and review-only", changes=overlap)
    return Policy(
        policy_id=policy_id,
        default_mode=mode,
        forbidden_changes=tuple(sorted(forbidden)),
        review_changes=tuple(sorted(review)),
    )


def parse_consumer(
    document: Any,
    *,
    expected_table: str,
    known_versions: tuple[str, ...],
    default_mode: str,
) -> ConsumerContract:
    if not isinstance(document, dict):
        raise _error("SG-LOAD-010", "consumer document must be a mapping")
    allowed = {
        "consumer_id", "table", "read_versions", "write_versions", "mode",
        "allowed_type_widening", "allow_partition_change", "severity_overrides",
        "description",
    }
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise _error("SG-LOAD-010", "unknown consumer attributes", attributes=unknown)
    consumer_id = _text(document.get("consumer_id"), "consumer_id", "SG-LOAD-010")
    if not _ID_RE.match(consumer_id):
        raise _error("SG-LOAD-010", "consumer_id is not a valid identifier", consumer_id=consumer_id)
    table = _text(document.get("table"), "consumer.table", "SG-LOAD-010")
    if table != expected_table:
        raise _error("SG-LOAD-010", "consumer table does not match manifest", expected=expected_table)
    read_versions = _string_list(document.get("read_versions"), "read_versions", "SG-LOAD-010")
    write_versions = _string_list(document.get("write_versions"), "write_versions", "SG-LOAD-010")
    declared = read_versions + write_versions
    if not declared:
        raise _error("SG-LOAD-010", "consumer must declare read_versions or write_versions")
    unknown_versions = sorted(set(declared) - set(known_versions))
    if unknown_versions:
        raise _error("SG-LOAD-010", "consumer references unknown versions", versions=unknown_versions)
    mode = document.get("mode", default_mode)
    if mode not in COMPAT_MODES:
        raise _error("SG-LOAD-005", "unknown compatibility mode", mode=mode)
    widening = _string_list(
        document.get("allowed_type_widening"), "allowed_type_widening", "SG-LOAD-010"
    )
    normalized_widening = []
    for entry in widening:
        try:
            normalized_widening.append(normalize_widening(entry))
        except SchemaGateError as exc:
            raise _error("SG-LOAD-010", exc.message, entry=entry) from exc
    allow_partition = document.get("allow_partition_change", False)
    if not isinstance(allow_partition, bool):
        raise _error("SG-LOAD-010", "allow_partition_change must be a boolean")
    overrides = document.get("severity_overrides", {})
    if not isinstance(overrides, dict):
        raise _error("SG-LOAD-010", "severity_overrides must be a mapping")
    normalized_overrides: dict[str, str] = {}
    for change_type, severity in overrides.items():
        if change_type not in CHANGE_TYPES:
            raise _error("SG-LOAD-010", "unknown change type in severity_overrides", change_type=change_type)
        if severity not in SEVERITIES:
            raise _error("SG-LOAD-010", "unknown severity in severity_overrides", severity=severity)
        normalized_overrides[change_type] = severity
    return ConsumerContract(
        consumer_id=consumer_id,
        table=table,
        read_versions=tuple(sorted(read_versions)),
        write_versions=tuple(sorted(write_versions)),
        mode=mode,
        allowed_type_widening=tuple(sorted(set(normalized_widening))),
        allow_partition_change=allow_partition,
        severity_overrides=dict(sorted(normalized_overrides.items())),
    )


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _error("SG-LOAD-009", f"{label} must be a positive integer")
    return value


def parse_partition_spec(
    document: Any,
    *,
    expected_version: str,
    expected_table: str,
) -> PartitionSpec:
    if not isinstance(document, dict):
        raise _error("SG-LOAD-009", "partition document must be a mapping")
    allowed = {
        "table", "version", "partition_keys", "sort_columns", "file_format", "description",
    }
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise _error("SG-LOAD-009", "unknown partition attributes", attributes=unknown)
    version = _text(document.get("version"), "partition.version", "SG-LOAD-009")
    table = _text(document.get("table"), "partition.table", "SG-LOAD-009")
    if version != expected_version:
        raise _error("SG-LOAD-009", "partition version does not match manifest", expected=expected_version)
    if table != expected_table:
        raise _error("SG-LOAD-009", "partition table does not match manifest", expected=expected_table)
    raw_keys = document.get("partition_keys", [])
    if not isinstance(raw_keys, list):
        raise _error("SG-LOAD-009", "partition_keys must be a list")
    keys: list[PartitionKey] = []
    seen_columns = set()
    for raw_key in raw_keys:
        if not isinstance(raw_key, dict):
            raise _error("SG-LOAD-009", "partition key entries must be mappings")
        key_allowed = {"column", "transform", "bucket_count", "truncate_width"}
        if set(raw_key) - key_allowed:
            raise _error("SG-LOAD-009", "unknown partition key attributes", key=raw_key.get("column"))
        column = _text(raw_key.get("column"), "partition key column", "SG-LOAD-009")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", column):
            raise _error("SG-LOAD-009", "partition column is not a simple identifier", column=column)
        if column in seen_columns:
            raise _error("SG-LOAD-009", "duplicate partition key column", column=column)
        seen_columns.add(column)
        transform = raw_key.get("transform", "identity")
        if transform not in PARTITION_TRANSFORMS:
            raise _error("SG-LOAD-009", "unknown partition transform", transform=transform)
        bucket_count = raw_key.get("bucket_count")
        truncate_width = raw_key.get("truncate_width")
        if transform == "identity":
            if bucket_count is not None or truncate_width is not None:
                raise _error("SG-LOAD-009", "identity transform takes no width/count", column=column)
        elif transform == "bucket":
            bucket_count = _positive_int(bucket_count, "bucket_count")
            if truncate_width is not None:
                raise _error("SG-LOAD-009", "bucket transform does not take truncate_width")
        else:
            truncate_width = _positive_int(truncate_width, "truncate_width")
            if bucket_count is not None:
                raise _error("SG-LOAD-009", "truncate transform does not take bucket_count")
        keys.append(PartitionKey(column, transform, bucket_count, truncate_width))
    sort_columns = _string_list(document.get("sort_columns"), "sort_columns", "SG-LOAD-009")
    file_format = document.get("file_format", "")
    if not isinstance(file_format, str):
        raise _error("SG-LOAD-009", "file_format must be a string")
    return PartitionSpec(
        version=version,
        table=table,
        partition_keys=tuple(keys),
        sort_columns=sort_columns,
        file_format=file_format.strip().lower(),
    )
