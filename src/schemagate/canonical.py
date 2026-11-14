"""Canonicalization, stable JSON serialization, and input fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .errors import SchemaGateError
from .models import CaseData, FieldSpec, PartitionSpec, SchemaVersion
from .type_rules import field_type_signature


def normalize_json_value(value: Any, *, path: str = "value") -> Any:
    """Validate and normalize a JSON-compatible value."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SchemaGateError("SG-LOAD-008", "default value must be finite", {"path": path})
        return value
    if isinstance(value, list):
        return [normalize_json_value(item, path=f"{path}[]") for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise SchemaGateError("SG-LOAD-008", "mapping keys must be strings", {"path": path})
            normalized[key] = normalize_json_value(value[key], path=f"{path}.{key}")
        return normalized
    raise SchemaGateError(
        "SG-LOAD-008",
        "value is not JSON-compatible",
        {"path": path, "python_type": type(value).__name__},
    )


def canonical_json(value: Any, *, indent: int | None = None) -> str:
    normalized = normalize_json_value(value)
    if indent is None:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
        allow_nan=False,
    )


def stable_json(value: Any, *, indent: int = 2) -> str:
    return canonical_json(value, indent=indent) + "\n"


def sha256_canonical(value: Any) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _field_payload(field: FieldSpec, include_order: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": field.name,
        "path": field.path,
        "type_signature": field_type_signature(field),
        "nullable": field.nullable,
        "has_default": field.has_default,
        "default": normalize_json_value(field.default),
        "field_id": field.field_id,
        "aliases": list(field.aliases),
        "doc": field.doc,
        "constraints": normalize_json_value(field.constraints),
        "children": [
            _field_payload(child, include_order)
            for child in sorted(field.children, key=lambda item: item.path)
        ],
    }
    if include_order:
        payload["order"] = field.order
    return payload


def _schema_payload(version: SchemaVersion, include_order: bool) -> dict[str, Any]:
    fields = list(version.fields)
    if not include_order:
        fields = sorted(fields, key=lambda item: item.path)
    return {
        "version": version.version,
        "table": version.table,
        "fields": [_field_payload(field, include_order) for field in fields],
    }


def schema_fingerprint(version: SchemaVersion) -> str:
    return sha256_canonical(_schema_payload(version, include_order=False))


def _partition_payload(spec: PartitionSpec | None) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
        "version": spec.version,
        "table": spec.table,
        "partition_keys": [
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


def case_payload(case: CaseData) -> dict[str, Any]:
    """Semantic input payload used for the idempotency fingerprint.
    Declared field and partition order are preserved on purpose: order changes
    are meaningful and must change the run identity.
    """
    versions = []
    for version in case.versions:
        payload = _schema_payload(version, include_order=True)
        payload["partition_spec"] = _partition_payload(case.partitions.get(version.version))
        versions.append(payload)
    return {
        "input_schema_version": 1,
        "case_id": case.case_id,
        "table": case.table,
        "description": case.description,
        "versions": versions,
        "consumers": [
            {
                "consumer_id": consumer.consumer_id,
                "table": consumer.table,
                "read_versions": sorted(consumer.read_versions),
                "write_versions": sorted(consumer.write_versions),
                "mode": consumer.mode,
                "allowed_type_widening": sorted(consumer.allowed_type_widening),
                "allow_partition_change": consumer.allow_partition_change,
                "severity_overrides": normalize_json_value(consumer.severity_overrides),
            }
            for consumer in sorted(case.consumers, key=lambda item: item.consumer_id)
        ],
        "policy": {
            "policy_id": case.policy.policy_id,
            "default_mode": case.policy.default_mode,
            "forbidden_changes": sorted(case.policy.forbidden_changes),
            "review_changes": sorted(case.policy.review_changes),
        },
    }


def input_fingerprint(case: CaseData) -> str:
    return sha256_canonical(case_payload(case))


