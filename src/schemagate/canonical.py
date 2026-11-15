"""Canonicalization, stable JSON serialization, and input fingerprints."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from .errors import SchemaGateError
from .models import CaseData, ConsumerContract, FieldSpec, PartitionSpec, SchemaVersion
from .naming import pair_id
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


def _version_payload(case: CaseData, version: SchemaVersion) -> dict[str, Any]:
    payload = _schema_payload(version, include_order=True)
    payload["partition_spec"] = _partition_payload(case.partitions.get(version.version))
    return payload


def _consumer_declaration_payload(consumer: ConsumerContract) -> dict[str, Any]:
    """Canonical declaration of a single consumer contract."""
    return {
        "consumer_id": consumer.consumer_id,
        "table": consumer.table,
        "read_versions": sorted(consumer.read_versions),
        "write_versions": sorted(consumer.write_versions),
        "mode": consumer.mode,
        "allowed_type_widening": sorted(consumer.allowed_type_widening),
        "allow_partition_change": consumer.allow_partition_change,
        "severity_overrides": normalize_json_value(consumer.severity_overrides),
    }


def _policy_payload(case: CaseData) -> dict[str, Any]:
    return {
        "policy_id": case.policy.policy_id,
        "default_mode": case.policy.default_mode,
        "forbidden_changes": sorted(case.policy.forbidden_changes),
        "review_changes": sorted(case.policy.review_changes),
    }


def case_payload(case: CaseData) -> dict[str, Any]:
    """Semantic input payload used for the idempotency fingerprint.
    Declared field and partition order are preserved on purpose: order changes
    are meaningful and must change the run identity.
    """
    return {
        "input_schema_version": 1,
        "case_id": case.case_id,
        "table": case.table,
        "description": case.description,
        "versions": [_version_payload(case, version) for version in case.versions],
        "consumers": [
            _consumer_declaration_payload(consumer)
            for consumer in sorted(case.consumers, key=lambda item: item.consumer_id)
        ],
        "policy": _policy_payload(case),
    }


def input_fingerprint(case: CaseData) -> str:
    return sha256_canonical(case_payload(case))


# ---------------------------------------------------------------------------
# Hierarchical impact fingerprints
#
# The four declaration domains (schema, partition, consumer, policy) each get
# their own sub-fingerprint, plus per-consumer and per-version-pair entries.
# Every sub-fingerprint is derived from the same canonical builders as the
# full input payload, so the breakdown partitions the input's semantic surface
# rather than re-interpreting it. The run id stays derived solely from the full
# input fingerprint; these fingerprints only explain *what* changed.
# ---------------------------------------------------------------------------


def _schema_domain_payload(case: CaseData) -> dict[str, Any]:
    return {
        "domain": "schema",
        "case_id": case.case_id,
        "table": case.table,
        "versions": [
            _schema_payload(version, include_order=True) for version in case.versions
        ],
    }


def _partition_domain_payload(case: CaseData) -> dict[str, Any]:
    return {
        "domain": "partition",
        "case_id": case.case_id,
        "table": case.table,
        "partition_specs": [
            {"version": version.version,
             "partition_spec": _partition_payload(case.partitions.get(version.version))}
            for version in case.versions
        ],
    }


def _consumer_domain_payload(case: CaseData) -> dict[str, Any]:
    return {
        "domain": "consumer",
        "case_id": case.case_id,
        "table": case.table,
        "consumers": [
            _consumer_declaration_payload(consumer)
            for consumer in sorted(case.consumers, key=lambda item: item.consumer_id)
        ],
    }


def _policy_domain_payload(case: CaseData) -> dict[str, Any]:
    payload = _policy_payload(case)
    payload["domain"] = "policy"
    payload["case_id"] = case.case_id
    payload["table"] = case.table
    return payload


def _pair_payload(case: CaseData, old: SchemaVersion, new: SchemaVersion) -> dict[str, Any]:
    """Semantics of one adjacent version pair.

    Covers both sides of the pair (each schema with declared field order) and
    the partition spec declared on each side, so any schema or partition
    change on the pair moves its sub-fingerprint.
    """
    return {
        "pair_id": pair_id(old.version, new.version),
        "table": case.table,
        "from_version": _version_payload(case, old),
        "to_version": _version_payload(case, new),
    }


def affected_pair_ids(case: CaseData, consumer: ConsumerContract) -> list[str]:
    """Pairs whose ``from_version`` is at or after the consumer's anchor.

    The anchor is the position of the latest version the consumer declares
    (read or write), mirroring :func:`compatibility.affected_changes`: a pair
    affects a consumer exactly when one of its changes would be evaluated.
    """
    positions = {version.version: index for index, version in enumerate(case.versions)}
    anchor = max(positions[version] for version in consumer.declared_versions)
    return [
        pair_id(case.versions[index].version, case.versions[index + 1].version)
        for index in range(len(case.versions) - 1)
        if index >= anchor
    ]


def fingerprint_breakdown(case: CaseData) -> dict[str, Any]:
    """Layered sub-fingerprints explaining which declaration domain changed.

    Structure::

        {
          "input_fingerprint": <full fingerprint, run identity source>,
          "domains": {
            "schema": "<sha256>", "partition": "<sha256>",
            "consumer": "<sha256>", "policy": "<sha256>"
          },
          "version_pairs": [
            {"pair_id", "from_version", "to_version", "fingerprint"}
          ],
          "consumers": [
            {"consumer_id", "fingerprint", "anchor_version",
             "affected_pair_ids": [...]}
          ]
        }
    """
    domain_fingerprints = {
        "schema": sha256_canonical(_schema_domain_payload(case)),
        "partition": sha256_canonical(_partition_domain_payload(case)),
        "consumer": sha256_canonical(_consumer_domain_payload(case)),
        "policy": sha256_canonical(_policy_domain_payload(case)),
    }
    pair_entries = []
    for index in range(len(case.versions) - 1):
        old, new = case.versions[index], case.versions[index + 1]
        pair_entries.append({
            "pair_id": pair_id(old.version, new.version),
            "from_version": old.version,
            "to_version": new.version,
            "fingerprint": sha256_canonical(_pair_payload(case, old, new)),
        })
    positions = {version.version: index for index, version in enumerate(case.versions)}
    consumer_entries = []
    for consumer in sorted(case.consumers, key=lambda item: item.consumer_id):
        anchor_index = max(positions[v] for v in consumer.declared_versions)
        consumer_entries.append({
            "consumer_id": consumer.consumer_id,
            "fingerprint": sha256_canonical(_consumer_declaration_payload(consumer)),
            "anchor_version": case.versions[anchor_index].version,
            "affected_pair_ids": affected_pair_ids(case, consumer),
        })
    return {
        "input_fingerprint": input_fingerprint(case),
        "domains": domain_fingerprints,
        "version_pairs": pair_entries,
        "consumers": consumer_entries,
    }


