"""Typed, immutable-ish domain objects shared by the engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import CHANGE_TYPES

# The declarative side(s) of a version pair that a change fact is observed on.
# ``old`` means the fact is only verifiable in the from-version, ``new`` means
# only in the to-version, and ``both`` means both sides carry evidence (for
# example a rename whose old and new names are both present).
EVIDENCE_DIRECTIONS = ("old", "new", "both")

# The regulated evidence direction for every change class.  ``None`` marks an
# aggregate whose direction is derived from its component facts:
# ``struct_field_change`` from nested child evidence and ``partition_change``
# from the two partition declarations it compares.  Destructive field facts are
# ``old``, additive field facts are ``new``, and every fact that is only
# provable by comparing both sides is ``both``.
CHANGE_EVIDENCE_DIRECTIONS: dict[str, str | None] = {
    "drop_field": "old",
    "add_field": "new",
    "struct_field_change": None,
    "partition_change": None,
    "rename_field": "both",
    "field_order_change": "both",
    "widen_type": "both",
    "narrow_type": "both",
    "type_change": "both",
    "nullable_to_true": "both",
    "nullable_to_false": "both",
    "default_added": "both",
    "default_removed": "both",
    "default_changed": "both",
    "element_type_change": "both",
    "decimal_change": "both",
    "timestamp_unit_change": "both",
    "constraint_change": "both",
}

assert set(CHANGE_EVIDENCE_DIRECTIONS) == set(CHANGE_TYPES), (
    "every change type must declare an evidence direction rule"
)


def evidence_version_for(direction: str, from_version: str, to_version: str) -> str:
    """Return the version whose structure holds the evidence for a fact.

    ``old`` facts are read from the from-version; ``new`` and ``both`` facts
    point at the to-version, which is where the current (post-change) value is
    declared.  Callers must only pass a declared direction.
    """
    if direction not in EVIDENCE_DIRECTIONS:
        raise ValueError(f"unknown evidence direction: {direction!r}")
    return from_version if direction == "old" else to_version


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
    # The declarative version that owns the evidence path, and the side of the
    # version pair that the fact is verifiable on.  ``evidence_direction`` is
    # one of ``EVIDENCE_DIRECTIONS``; an empty or unknown value is treated as
    # uninterpretable by full-mode compatibility evaluation.
    evidence_version: str = ""
    evidence_direction: str = ""


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


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    decision: str
    summary: dict[str, Any]
    reused: bool
    attempt_count: int
    result: dict[str, Any]
