"""Compatibility policy evaluation for each consumer and change."""

from __future__ import annotations

from .config import SEVERITY_RANK
from .models import (
    CaseData,
    Change,
    ConsumerContract,
    ConsumerVerdict,
    Finding,
    Policy,
    RuleOutcome,
)

_CONSTRAINT_DIRECTIONS = ("widen", "narrow", "incomparable")


def _outcome(severity: str, rule_id: str, message: str, **details: object) -> RuleOutcome:
    return RuleOutcome(severity, rule_id, message, dict(details))


def _constraint_direction(change: Change, child: dict | None = None) -> str:
    """Direction carried by a constraint change or one of its children."""
    source = child if child is not None else change.details
    direction = source.get("direction")
    return direction if direction in _CONSTRAINT_DIRECTIONS else "incomparable"


def _constraint_review(path: str, direction: str, review_rule: str) -> RuleOutcome:
    return _outcome(
        "REVIEW", review_rule,
        f"field constraints changed at {path} ({direction})",
        direction=direction,
    )


def _constraint_outcome(consumer: ConsumerContract, path: str, direction: str,
                        review_rule: str) -> RuleOutcome:
    """Grade one constraint determination against a declared path contract."""
    acceptance = consumer.acceptance_for(path)
    if acceptance is None:
        return _constraint_review(path, direction, review_rule)
    if acceptance.accepts(direction):
        return _outcome(
            "PASS", "SG-COMP-120",
            f"consumer contract accepts {direction} constraint change at {path}",
            direction=direction, accepted=list(acceptance.accepted),
        )
    return _outcome(
        "BLOCK", "SG-COMP-121",
        f"consumer contract at {path} does not accept {direction} constraint change",
        direction=direction, accepted=list(acceptance.accepted),
    )


def _constraint_rule(change: Change, consumer: ConsumerContract,
                     review_rule: str) -> RuleOutcome:
    """Grade a constraint change by every changed path it carries."""
    children = change.details.get("children")
    if not isinstance(children, list) or not children:
        return _constraint_outcome(
            consumer, change.field_path, _constraint_direction(change), review_rule
        )
    outcomes = []
    for child in children:
        if not isinstance(child, dict) or not isinstance(child.get("path"), str):
            continue
        outcomes.append(_constraint_outcome(
            consumer, child["path"], _constraint_direction(change, child),
            review_rule,
        ))
    if not outcomes:
        return _constraint_outcome(
            consumer, change.field_path, _constraint_direction(change), review_rule
        )
    return max(outcomes, key=lambda item: SEVERITY_RANK[item.severity])


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
        return _constraint_rule(change, consumer, "SG-COMP-119")
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
        return _constraint_rule(change, consumer, "SG-COMP-219")
    return _outcome("REVIEW", "SG-COMP-999", f"unclassified change at {path}")


def _full(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    backward = _backward(change, consumer)
    forward = _forward(change, consumer)
    details = {
        "backward_rule_id": backward.rule_id,
        "forward_rule_id": forward.rule_id,
        "backward_severity": backward.severity,
        "forward_severity": forward.severity,
    }
    if backward.severity == forward.severity == "PASS":
        return _outcome("PASS", "SG-COMP-300", f"full mode is compatible for {change.field_path}", **details)
    if backward.severity == forward.severity == "REVIEW":
        return _outcome("REVIEW", "SG-COMP-301", f"full mode requires review for {change.field_path}", **details)
    if backward.severity == forward.severity == "BLOCK":
        return _outcome("BLOCK", "SG-COMP-302", f"full mode blocks change at {change.field_path}", **details)
    chosen = backward if SEVERITY_RANK[backward.severity] > SEVERITY_RANK[forward.severity] else forward
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
