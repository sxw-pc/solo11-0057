"""Small builders shared by unit tests."""

from __future__ import annotations

from typing import Any

import yaml

from schemagate.diff_engine import diff_versions
from schemagate.models import (
    CaseData,
    Change,
    ConstraintAcceptance,
    ConsumerContract,
    PartitionSpec,
    Policy,
)
from schemagate.naming import change_id, field_evidence, pair_id
from schemagate.schema_parser import parse_schema_document


def field(name: str, type_name: str, **kwargs: Any) -> dict[str, Any]:
    payload = {"name": name, "type": type_name}
    payload.update(kwargs)
    return payload


def schema(version: str, fields: list[dict[str, Any]], table: str = "demo.unit") -> Any:
    return parse_schema_document(
        {"table": table, "version": version, "fields": fields},
        expected_version=version,
        expected_table=table,
    )


def make_case(
    schemas: list[Any],
    *,
    partitions: dict[str, PartitionSpec | None] | None = None,
    consumers: tuple[ConsumerContract, ...] = (),
    policy: Policy | None = None,
    table: str = "demo.unit",
) -> CaseData:
    return CaseData(
        case_id="unit_case",
        table=table,
        description="unit",
        versions=tuple(schemas),
        partitions=partitions or {item.version: None for item in schemas},
        consumers=consumers,
        policy=policy or Policy("default", "backward"),
    )


def make_change(
    change_type: str,
    path: str = "value",
    *,
    details: dict[str, Any] | None = None,
    from_version: str = "v1",
    to_version: str = "v2",
    table: str = "demo.unit",
) -> Change:
    pair = pair_id(from_version, to_version)
    return Change(
        change_id=change_id(table, pair, path, change_type),
        pair_id=pair,
        from_version=from_version,
        to_version=to_version,
        change_type=change_type,
        field_path=path,
        evidence_path=field_evidence(table, to_version, path, "type"),
        summary=f"{change_type} at {path}",
        details=details or {},
    )


def make_consumer(
    mode: str,
    *,
    consumer_id: str = "reader",
    read_versions: tuple[str, ...] = ("v1",),
    write_versions: tuple[str, ...] = (),
    allowed: tuple[str, ...] = (),
    allow_partition: bool = False,
    overrides: dict[str, str] | None = None,
    acceptance: tuple[ConstraintAcceptance, ...] = (),
) -> ConsumerContract:
    return ConsumerContract(
        consumer_id=consumer_id,
        table="demo.unit",
        read_versions=read_versions,
        write_versions=write_versions,
        mode=mode,
        allowed_type_widening=allowed,
        allow_partition_change=allow_partition,
        severity_overrides=overrides or {},
        constraint_acceptance=acceptance,
    )


def diff_docs(
    old_doc: dict[str, Any],
    new_doc: dict[str, Any],
    *,
    old_partition: PartitionSpec | None = None,
    new_partition: PartitionSpec | None = None,
    table: str = "demo.unit",
) -> tuple[Change, ...]:
    old = parse_schema_document(old_doc, expected_table=table)
    new = parse_schema_document(new_doc, expected_table=table)
    case = make_case(
        [old, new],
        partitions={old.version: old_partition, new.version: new_partition},
        table=table,
    )
    return diff_versions(case, old, new)


def dump_yaml(path: Any, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
