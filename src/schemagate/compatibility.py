"""Compatibility policy evaluation for each consumer and change."""

from __future__ import annotations

from dataclasses import replace

from .config import SEVERITY_RANK
from .models import (
    CaseData,
    Change,
    ConsumerContract,
    ConsumerVerdict,
    Finding,
    Policy,
    RuleOutcome,
    WindowVerdict,
)


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


def _version_positions(case: CaseData) -> dict[str, int]:
    return {version.version: index for index, version in enumerate(case.versions)}


def window_states(
    case: CaseData, consumer: ConsumerContract
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (read_status_by_pair, write_status_by_pair).

    Each value is one of:

    * ``exposed``  — the pair sits on the open window boundary for that side;
    * ``covered``  — both endpoints belong to the declared span, the consumer
      has already adopted through this transition;
    * ``excluded`` — the declared window never reaches this pair.

    Read and write windows are deliberately asymmetric. A reader's window
    opens at its highest declared version, whereas a writer's window opens on
    the inbound edge of its lowest declared version (the cutover pair), which
    remains exposed; only pairs strictly inside a multi-version write span are
    covered.
    """
    positions = _version_positions(case)
    read_states: dict[str, str] = {}
    write_states: dict[str, str] = {}
    read_indices = sorted(positions[version] for version in consumer.read_versions)
    write_indices = sorted(positions[version] for version in consumer.write_versions)
    read_min = read_indices[0] if read_indices else None
    read_max = read_indices[-1] if read_indices else None
    write_min = write_indices[0] if write_indices else None
    write_max = write_indices[-1] if write_indices else None
    for index in range(len(case.versions) - 1):
        pair = f"{case.versions[index].version}->{case.versions[index + 1].version}"
        if read_indices:
            if index < read_min:
                read_states[pair] = "excluded"
            elif index < read_max:
                read_states[pair] = "covered"
            else:
                read_states[pair] = "exposed"
        else:
            read_states[pair] = "excluded"
        if write_indices:
            if index < write_min - 1:
                write_states[pair] = "excluded"
            elif index < write_max:
                # The inbound edge of the lowest declared write version is the
                # writer's cutover: it stays exposed even though its target
                # version is declared. Internal spans are covered.
                write_states[pair] = "exposed" if index == write_min - 1 else "covered"
            else:
                write_states[pair] = "exposed"
        else:
            write_states[pair] = "excluded"
    return read_states, write_states


def affected_changes(case: CaseData, consumer: ConsumerContract,
                     changes: tuple[Change, ...]) -> tuple[Change, ...]:
    """Changes on any pair exposed on at least one declared side."""
    read_states, write_states = window_states(case, consumer)
    in_window = {
        pair for pair in read_states
        if "exposed" in (read_states[pair], write_states[pair])
    }
    return tuple(change for change in changes if change.pair_id in in_window)


def _side_severity_for(finding: Finding, direction: str) -> str:
    """Directional severity from full-mode details; overall severity otherwise."""
    key = f"{'backward' if direction == 'read' else 'forward'}_severity"
    return str(finding.details.get(key, finding.severity))


def _shadow_pass(
    findings: list[Finding],
    pair_order: list[str],
    direction: str,
) -> dict[str, set[str]]:
    """Return change IDs mooted by an upstream BLOCK inside the same window.

    Shadowing is a window-containment relation, not a severity ``max``: the
    upstream BLOCK pair and the downstream pair must both be exposed on this
    side. When the upstream pair is outside the window, downstream findings
    stay visible.
    """
    status_attr = f"{direction}_status"
    blocked_upstream: set[str] = set()
    shadowed_ids: dict[str, set[str]] = {pair: set() for pair in pair_order}
    for pair in pair_order:
        pair_findings = [item for item in findings if item.pair_id == pair]
        if blocked_upstream:
            for item in pair_findings:
                if getattr(item, status_attr) == "exposed":
                    shadowed_ids[pair].add(item.change_id)
        if any(
            getattr(item, status_attr) == "exposed"
            and _side_severity_for(item, direction) == "BLOCK"
            and item.change_id not in shadowed_ids[pair]
            for item in pair_findings
        ):
            blocked_upstream.add(pair)
    return shadowed_ids


def evaluate_case(case: CaseData, changes: tuple[Change, ...]) -> tuple[ConsumerVerdict, ...]:
    positions = _version_positions(case)
    pair_order = [
        f"{case.versions[index].version}->{case.versions[index + 1].version}"
        for index in range(len(case.versions) - 1)
    ]
    verdicts = []
    for consumer in sorted(case.consumers, key=lambda item: item.consumer_id):
        read_states, write_states = window_states(case, consumer)
        findings: list[Finding] = []
        read_outcomes: dict[str, list[Finding]] = {pair: [] for pair in pair_order}
        write_outcomes: dict[str, list[Finding]] = {pair: [] for pair in pair_order}
        for change in changes:
            read_state = read_states[change.pair_id]
            write_state = write_states[change.pair_id]
            if read_state != "exposed" and write_state != "exposed":
                continue
            outcome = evaluate_change(consumer, case.policy, change)
            finding = _finding(consumer.consumer_id, outcome, change)
            finding = replace(finding, read_status=read_state, write_status=write_state)
            if read_state == "exposed":
                read_outcomes[change.pair_id].append(finding)
            if write_state == "exposed":
                write_outcomes[change.pair_id].append(finding)
            findings.append(finding)
        read_shadowed = _shadow_pass(findings, pair_order, "read")
        write_shadowed = _shadow_pass(findings, pair_order, "write")
        for index, finding in enumerate(findings):
            is_read_shadow = finding.change_id in read_shadowed.get(finding.pair_id, set())
            is_write_shadow = finding.change_id in write_shadowed.get(finding.pair_id, set())
            directions = finding.applicable_directions
            findings[index] = replace(
                finding,
                read_shadow=is_read_shadow,
                write_shadow=is_write_shadow,
                downgrade_shadow=bool(directions) and all(
                    (is_read_shadow if direction == "read" else is_write_shadow)
                    for direction in directions
                ),
            )
        window_verdicts = []
        for index, pair in enumerate(pair_order):
            from_version, to_version = pair.split("->", 1)
            read_verdict = "N/A"
            write_verdict = "N/A"
            if read_states[pair] == "exposed":
                read_verdict = max(
                    (_side_severity_for(item, "read") for item in read_outcomes[pair]),
                    key=lambda value: SEVERITY_RANK[value], default="PASS",
                )
            if write_states[pair] == "exposed":
                write_verdict = max(
                    (_side_severity_for(item, "write") for item in write_outcomes[pair]),
                    key=lambda value: SEVERITY_RANK[value], default="PASS",
                )
            window_verdicts.append(WindowVerdict(
                consumer_id=consumer.consumer_id,
                pair_id=pair,
                from_version=from_version,
                to_version=to_version,
                read_status=read_states[pair],
                write_status=write_states[pair],
                read_verdict=read_verdict,
                write_verdict=write_verdict,
                read_shadowed_change_ids=tuple(sorted(read_shadowed[pair])),
                write_shadowed_change_ids=tuple(sorted(write_shadowed[pair])),
            ))
        declared = consumer.declared_versions
        anchor_version = case.versions[
            max(positions[version] for version in declared)
        ].version
        read_anchor = (
            case.versions[max(positions[v] for v in consumer.read_versions)].version
            if consumer.read_versions else ""
        )
        write_anchor = (
            case.versions[max(positions[v] for v in consumer.write_versions)].version
            if consumer.write_versions else ""
        )
        if not findings:
            findings.append(_finding(
                consumer.consumer_id,
                _outcome("PASS", "SG-COMP-000",
                         "no pending schema changes for the consumer's declared versions"),
                evidence_path=f"cases.{case.case_id}.consumers.{consumer.consumer_id}",
                details={"anchor_version": anchor_version},
            ))
        decision = max(
            (item.severity for item in findings if item.effective),
            key=lambda value: SEVERITY_RANK[value], default="PASS",
        )
        verdicts.append(ConsumerVerdict(
            consumer_id=consumer.consumer_id, mode=consumer.mode,
            anchor_version=anchor_version, decision=decision,
            findings=tuple(findings), read_versions=consumer.read_versions,
            write_versions=consumer.write_versions,
            read_anchor_version=read_anchor,
            write_anchor_version=write_anchor,
            window_verdicts=tuple(window_verdicts),
            shadowed_finding_count=sum(item.downgrade_shadow for item in findings),
        ))
    return tuple(verdicts)
