"""Typed, immutable-ish domain objects shared by the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TypeSpec:
    """A normalized type descriptor.
    Struct children live on FieldSpec.children so they can carry full field
    metadata; list and map carry their nested type descriptors here.
    """
    kind: str
    precision: int | None = None
    scale: int | None = None
    element: TypeSpec | None = None
    key: TypeSpec | None = None
    value: TypeSpec | None = None


@dataclass(frozen=True)
class FieldSpec:
    name: str
    path: str
    type_spec: TypeSpec
    nullable: bool = True
    has_default: bool = False
    default: Any = None
    field_id: int | None = None
    aliases: tuple[str, ...] = ()
    doc: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    children: tuple[FieldSpec, ...] = ()
    order: int = 0
    @property
    def required(self) -> bool:
        return (not self.nullable) and (not self.has_default or self.default is None)


@dataclass(frozen=True)
class SchemaVersion:
    version: str
    table: str
    fields: tuple[FieldSpec, ...]
    schema_fingerprint: str = ""
    def field_index(self) -> dict[str, FieldSpec]:
        """Flat path index over top-level and nested fields."""
        index: dict[str, FieldSpec] = {}
        def visit(fields: tuple[FieldSpec, ...]) -> None:
            for item in fields:
                index[item.path] = item
                visit(item.children)
        visit(self.fields)
        return index


@dataclass(frozen=True)
class PartitionKey:
    column: str
    transform: str
    bucket_count: int | None = None
    truncate_width: int | None = None


@dataclass(frozen=True)
class PartitionSpec:
    version: str
    table: str
    partition_keys: tuple[PartitionKey, ...] = ()
    sort_columns: tuple[str, ...] = ()
    file_format: str = ""


@dataclass(frozen=True)
class Policy:
    policy_id: str
    default_mode: str
    forbidden_changes: tuple[str, ...] = ()
    review_changes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConsumerContract:
    consumer_id: str
    table: str
    read_versions: tuple[str, ...] = ()
    write_versions: tuple[str, ...] = ()
    mode: str = "backward"
    allowed_type_widening: tuple[str, ...] = ()
    allow_partition_change: bool = False
    severity_overrides: dict[str, str] = field(default_factory=dict)
    @property
    def declared_versions(self) -> tuple[str, ...]:
        return self.read_versions + self.write_versions


@dataclass(frozen=True)
class CaseData:
    case_id: str
    table: str
    description: str
    versions: tuple[SchemaVersion, ...]
    partitions: dict[str, PartitionSpec | None]
    consumers: tuple[ConsumerContract, ...]
    policy: Policy
    input_fingerprint: str = ""


@dataclass(frozen=True)
class Change:
    change_id: str
    pair_id: str
    from_version: str
    to_version: str
    change_type: str
    field_path: str
    evidence_path: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleOutcome:
    severity: str
    rule_id: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    consumer_id: str
    severity: str
    rule_id: str
    change_id: str
    pair_id: str
    change_type: str
    field_path: str
    evidence_path: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConsumerVerdict:
    consumer_id: str
    mode: str
    anchor_version: str
    decision: str
    findings: tuple[Finding, ...] = ()
    read_versions: tuple[str, ...] = ()
    write_versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationStep:
    number: int
    target_version: str
    action: str
    affected_consumers: tuple[str, ...]
    prerequisites: tuple[str, ...]
    reversible: bool
    status: str
    blocking_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RollbackHint:
    for_step: int
    version_pair: str
    rollback_to: str
    retain_fields: tuple[str, ...]
    consumers_to_rollback: tuple[str, ...]
    note: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    decision: str
    summary: dict[str, Any]
    reused: bool
    attempt_count: int
    result: dict[str, Any]
