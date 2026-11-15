"""Compatibility policy evaluation for each consumer and change."""

from __future__ import annotations

from .config import SEVERITY_RANK
from .models import (
    EVIDENCE_DIRECTIONS,
    CaseData,
    Change,
    ConsumerContract,
    ConsumerVerdict,
    Finding,
    Policy,
    RuleOutcome,
)

# A direction is evaluated by the branch that can actually prove its failure.
_BRANCH_DIRECTIONS = {
    "backward": frozenset({"old", "both"}),
    "forward": frozenset({"new", "both"}),
}
_NOT_APPLICABLE = "not-applicable"


def _outcome(severity: str, rule_id: str, message: str, **details: object) -> RuleOutcome:
    return RuleOutcome(severity, rule_id, message, dict(details))


def _allowed_widening(consumer: ConsumerContract, change: Change) -> bool:
    old_type = change.details.get("old_type")
    new_type = change.details.get("new_type")
    return isinstance(old_type, str) and isinstance(new_type, str) and (
        f"{old_type}->{new_type}" in consumer.allowed_type_widening
    )


def _backward(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    kind, path = change.change_type, change.field_path
    if kind == "add_field":
        if change.details.get("required"):
            return _outcome(
                "BLOCK", "SG-COMP-101",
                f"backward mode blocks a new required field at {path}",
            )
        return _outcome("PASS", "SG-COMP-102", f"optional field added at {path}")
    if kind == "drop_field":
        return _outcome("BLOCK", "SG-COMP-103", f"backward mode blocks field removal at {path}")
    if kind == "rename_field":
        return _outcome("REVIEW", "SG-COMP-104", f"explicit field rename requires review at {path}")
    if kind == "widen_type":
        if _allowed_widening(consumer, change):
            return _outcome("PASS", "SG-COMP-105", f"allowed type widening at {path}")
        return _outcome("REVIEW", "SG-COMP-106", f"type widening not declared for {path}")
    if kind == "narrow_type":
        return _outcome("BLOCK", "SG-COMP-107", f"backward mode blocks type narrowing at {path}")
    if kind == "type_change":
        return _outcome("BLOCK", "SG-COMP-108", f"backward mode blocks incompatible type change at {path}")
    if kind == "nullable_to_true":
        return _outcome("PASS", "SG-COMP-109", f"nullable relaxation accepted at {path}")
    if kind == "nullable_to_false":
        return _outcome("BLOCK", "SG-COMP-110", f"backward mode blocks nullable tightening at {path}")
    if kind == "default_added":
        return _outcome("PASS", "SG-COMP-111", f"default added at {path}")
    if kind == "default_removed":
        return _outcome("REVIEW", "SG-COMP-112", f"default removal requires review at {path}")
    if kind == "default_changed":
        return _outcome("REVIEW", "SG-COMP-113", f"default change requires review at {path}")
    if kind == "field_order_change":
        return _outcome("REVIEW", "SG-COMP-114", f"field order changed at {path}")
    if kind == "decimal_change":
        return _outcome("REVIEW", "SG-COMP-115", f"decimal precision or scale changed at {path}")
    if kind == "timestamp_unit_change":
        return _outcome("BLOCK", "SG-COMP-116", f"timestamp unit changed at {path}")
    if kind == "element_type_change":
        return _outcome("BLOCK", "SG-COMP-117", f"collection element type changed at {path}")
    if kind == "struct_field_change":
        return _outcome("REVIEW", "SG-COMP-118", f"nested struct fields changed at {path}")
    if kind == "constraint_change":
        return _outcome("REVIEW", "SG-COMP-119", f"field constraints changed at {path}")
    return _outcome("REVIEW", "SG-COMP-999", f"unclassified change at {path}")


def _forward(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    kind, path = change.change_type, change.field_path
    if kind == "add_field":
        if change.details.get("required"):
            return _outcome(
                "BLOCK", "SG-COMP-201",
                f"forward mode blocks a new required field at {path}",
            )
        return _outcome("PASS", "SG-COMP-202", f"optional field added at {path}")
    if kind == "drop_field":
        return _outcome("BLOCK", "SG-COMP-203", f"forward mode blocks field removal at {path}")
    if kind == "rename_field":
        return _outcome("REVIEW", "SG-COMP-204", f"explicit field rename requires review at {path}")
    if kind == "widen_type":
        if _allowed_widening(consumer, change):
            return _outcome("PASS", "SG-COMP-205", f"allowed type widening at {path}")
        return _outcome("REVIEW", "SG-COMP-206", f"type widening not declared for {path}")
    if kind == "narrow_type":
        return _outcome("BLOCK", "SG-COMP-207", f"forward mode blocks type narrowing at {path}")
    if kind == "type_change":
        return _outcome("BLOCK", "SG-COMP-208", f"forward mode blocks incompatible type change at {path}")
    if kind == "nullable_to_true":
        return _outcome("PASS", "SG-COMP-209", f"nullable relaxation accepted at {path}")
    if kind == "nullable_to_false":
        return _outcome("BLOCK", "SG-COMP-210", f"forward mode blocks nullable tightening at {path}")
    if kind == "default_added":
        return _outcome("REVIEW", "SG-COMP-211", f"default added at {path}")
    if kind == "default_removed":
        return _outcome("REVIEW", "SG-COMP-212", f"default removal requires review at {path}")
    if kind == "default_changed":
        return _outcome("REVIEW", "SG-COMP-213", f"default change requires review at {path}")
    if kind == "field_order_change":
        return _outcome("REVIEW", "SG-COMP-214", f"field order changed at {path}")
    if kind == "decimal_change":
        return _outcome("REVIEW", "SG-COMP-215", f"decimal precision or scale changed at {path}")
    if kind == "timestamp_unit_change":
        return _outcome("BLOCK", "SG-COMP-216", f"timestamp unit changed at {path}")
    if kind == "element_type_change":
        return _outcome("BLOCK", "SG-COMP-217", f"collection element type changed at {path}")
    if kind == "struct_field_change":
        return _outcome("REVIEW", "SG-COMP-218", f"nested struct fields changed at {path}")
    if kind == "constraint_change":
        return _outcome("REVIEW", "SG-COMP-219", f"field constraints changed at {path}")
    return _outcome("REVIEW", "SG-COMP-999", f"unclassified change at {path}")


def _direction_known(change: Change) -> bool:
    """A direction is interpretable when its version agrees with the pair.

    A missing ``evidence_version`` is allowed for records built outside the
    diff engine; an explicit version that contradicts the direction is not.
    """
    if not (
        isinstance(change.evidence_direction, str)
        and change.evidence_direction in EVIDENCE_DIRECTIONS
    ):
        return False
    if not isinstance(change.evidence_version, str) or not change.evidence_version:
        return True
    expected = (
        change.from_version
        if change.evidence_direction == "old"
        else change.to_version
    )
    return change.evidence_version == expected


def _changed_paths_consistent(change: Change) -> bool:
    """Verify the struct aggregate's grouped paths can explain its direction."""
    if change.change_type != "struct_field_change":
        return True
    groups = change.details.get("changed_paths")
    if not isinstance(groups, dict) or not groups:
        return False
    if not set(groups) <= set(EVIDENCE_DIRECTIONS):
        return False
    populated: dict[str, list] = {}
    for direction, paths in groups.items():
        if not isinstance(paths, list):
            return False
        if not paths:
            continue
        if not all(isinstance(path, str) and path for path in paths):
            return False
        populated[direction] = paths
    if not populated:
        return False
    sides = set(populated)
    if change.evidence_direction == "old":
        return sides == {"old"}
    if change.evidence_direction == "new":
        return sides == {"new"}
    if change.evidence_direction == "both":
        return "both" in sides or {"old", "new"} <= sides
    return False


def _direction_review(direction: str, change: Change, reason: str) -> RuleOutcome:
    return _outcome(
        "REVIEW", "SG-COMP-304",
        f"full mode cannot interpret {change.change_type} evidence direction "
        f"for the {direction} branch at {change.field_path}; treating it as REVIEW",
        branch=direction,
        evidence_version=change.evidence_version,
        evidence_direction=change.evidence_direction,
        reason=reason,
    )


def _uninterpretable_full(
    change: Change, details: dict[str, object], reason: str
) -> RuleOutcome:
    """Neither direction can explain this change, so both branches are REVIEW."""
    outcome = _direction_review("backward", change, reason)
    details["reason"] = reason
    for direction in ("backward", "forward"):
        details[f"{direction}_rule_id"] = outcome.rule_id
        details[f"{direction}_severity"] = outcome.severity
        details[f"{direction}_applicability"] = "direction-uninterpretable"
    return _outcome(outcome.severity, outcome.rule_id, outcome.message, **details)


def _full(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    details = {"evidence_version": change.evidence_version,
               "evidence_direction": change.evidence_direction}
    if not _direction_known(change):
        return _uninterpretable_full(change, details, "unknown evidence direction")
    if not _changed_paths_consistent(change):
        return _uninterpretable_full(
            change, details, "grouped changed_paths disagree with the aggregate direction"
        )
    branches = {}
    for direction, rules in (("backward", _backward), ("forward", _forward)):
        in_scope = change.evidence_direction in _BRANCH_DIRECTIONS[direction]
        details[f"{direction}_applicability"] = "evaluated" if in_scope else _NOT_APPLICABLE
        if not in_scope:
            # This branch has no evidence it could act on, so it must not
            # contribute a severity to the full-mode aggregate.
            details[f"{direction}_rule_id"] = _NOT_APPLICABLE
            details[f"{direction}_severity"] = _NOT_APPLICABLE
            continue
        outcome = rules(change, consumer)
        branches[direction] = outcome
        details[f"{direction}_rule_id"] = outcome.rule_id
        details[f"{direction}_severity"] = outcome.severity
    if len(branches) == 1:
        # Only one side of the transition carries evidence for this fact, so
        # the full verdict is exactly that direction's verdict.
        (direction, outcome), = branches.items()
        return _outcome(
            outcome.severity,
            outcome.rule_id,
            outcome.message + f" (full mode evaluated only the {direction} direction)",
            **details,
        )
    severities = {outcome.severity for outcome in branches.values()}
    if severities == {"PASS"}:
        return _outcome("PASS", "SG-COMP-300", f"full mode is compatible for {change.field_path}", **details)
    if severities == {"REVIEW"}:
        return _outcome("REVIEW", "SG-COMP-301", f"full mode requires review for {change.field_path}", **details)
    if severities == {"BLOCK"}:
        return _outcome("BLOCK", "SG-COMP-302", f"full mode blocks change at {change.field_path}", **details)
    chosen = max(branches.values(), key=lambda item: SEVERITY_RANK[item.severity])
    return _outcome(
        chosen.severity,
        chosen.rule_id,
        chosen.message + " (full mode evaluates both directions)",
        **details,
    )


def _base_rule(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    if consumer.mode == "none":
        return _outcome(
            "PASS", "SG-COMP-400",
            f"mode none records change at {change.field_path} without blocking",
        )
    if consumer.mode == "backward":
        return _backward(change, consumer)
    if consumer.mode == "forward":
        return _forward(change, consumer)
    if consumer.mode == "full":
        return _full(change, consumer)
    raise AssertionError(f"unvalidated compatibility mode: {consumer.mode}")


def evaluate_change(consumer: ConsumerContract, policy: Policy, change: Change) -> RuleOutcome:
    """Apply policy precedence and return the final, explainable outcome."""
    if change.change_type == "partition_change":
        if "partition_change" in policy.forbidden_changes:
            return _outcome(
                "BLOCK", "SG-POL-001",
                f"policy forbids partition changes at {change.field_path}",
            )
        override = consumer.severity_overrides.get("partition_change")
        if override is not None:
            return _outcome(
                override, "SG-POL-003",
                f"consumer override sets partition_change to {override}",
                base_rule_id="SG-PART-001",
            )
        if consumer.allow_partition_change:
            return _outcome(
                "PASS", "SG-PART-002",
                f"consumer explicitly allows partition changes at {change.field_path}",
            )
        return _outcome(
            "REVIEW", "SG-PART-001",
            f"partition change requires review at {change.field_path}",
        )
    base = _base_rule(change, consumer)
    if consumer.mode == "none":
        return base
    if change.change_type in policy.forbidden_changes:
        return _outcome(
            "BLOCK", "SG-POL-001",
            f"policy forbids {change.change_type} at {change.field_path}",
            base_rule_id=base.rule_id,
            base_severity=base.severity,
        )
    override = consumer.severity_overrides.get(change.change_type)
    if override is not None and override != base.severity:
        return _outcome(
            override, "SG-POL-003",
            f"consumer override sets {change.change_type} from {base.severity} to {override}",
            base_rule_id=base.rule_id,
            base_severity=base.severity,
        )
    if base.severity == "PASS" and change.change_type in policy.review_changes:
        return _outcome(
            "REVIEW", "SG-POL-002",
            f"policy requires review for {change.change_type} at {change.field_path}",
            base_rule_id=base.rule_id,
        )
    return base


def _finding(consumer_id: str, outcome: RuleOutcome, change: Change | None = None,
             *, evidence_path: str = "", details: dict | None = None) -> Finding:
    if change is None:
        return Finding(
            consumer_id=consumer_id, severity=outcome.severity,
            rule_id=outcome.rule_id, change_id="no-change", pair_id="",
            change_type="no_change", field_path="", evidence_path=evidence_path,
            message=outcome.message, details=details or outcome.details,
        )
    return Finding(
        consumer_id=consumer_id, severity=outcome.severity, rule_id=outcome.rule_id,
        change_id=change.change_id, pair_id=change.pair_id,
        change_type=change.change_type, field_path=change.field_path,
        evidence_path=change.evidence_path, message=outcome.message,
        details=outcome.details,
    )


def affected_changes(case: CaseData, consumer: ConsumerContract,
                     changes: tuple[Change, ...]) -> tuple[Change, ...]:
    positions = {version.version: index for index, version in enumerate(case.versions)}
    anchor = max(positions[version] for version in consumer.declared_versions)
    return tuple(c for c in changes if positions[c.from_version] >= anchor)


def evaluate_case(case: CaseData, changes: tuple[Change, ...]) -> tuple[ConsumerVerdict, ...]:
    positions = {version.version: index for index, version in enumerate(case.versions)}
    verdicts = []
    for consumer in sorted(case.consumers, key=lambda item: item.consumer_id):
        anchor_index = max(positions[v] for v in consumer.declared_versions)
        anchor_version = case.versions[anchor_index].version
        findings = [
            _finding(consumer.consumer_id, evaluate_change(consumer, case.policy, change),
                     change)
            for change in affected_changes(case, consumer, changes)
        ]
        if not findings:
            findings.append(_finding(
                consumer.consumer_id,
                _outcome("PASS", "SG-COMP-000",
                         "no pending schema changes for the consumer's declared versions"),
                evidence_path=f"cases.{case.case_id}.consumers.{consumer.consumer_id}",
                details={"anchor_version": anchor_version},
            ))
        decision = max((item.severity for item in findings),
                       key=lambda value: SEVERITY_RANK[value], default="PASS")
        verdicts.append(ConsumerVerdict(
            consumer_id=consumer.consumer_id, mode=consumer.mode,
            anchor_version=anchor_version, decision=decision,
            findings=tuple(findings), read_versions=consumer.read_versions,
            write_versions=consumer.write_versions,
        ))
    return tuple(verdicts)
