"""Advisory migration ordering and rollback hints; nothing is executed."""

from __future__ import annotations

from .compatibility import (
    build_field_identity,
    consumer_rollback_versions,
    merge_reuse_annotations,
    rollback_retain_plan,
)
from .config import SEVERITY_RANK
from .models import (
    CaseData,
    Change,
    ConsumerVerdict,
    MigrationStep,
    RollbackHint,
)


def _findings_for_pair(
    verdicts: tuple[ConsumerVerdict, ...], pair: str, consumers: tuple[str, ...]
) -> list:
    allowed = set(consumers)
    findings = []
    for verdict in verdicts:
        if verdict.consumer_id not in allowed:
            continue
        findings.extend(
            finding for finding in verdict.findings if finding.pair_id == pair
        )
    return findings


def _status(findings: list) -> str:
    severities = {finding.severity for finding in findings}
    if "BLOCK" in severities:
        return "blocked"
    if "REVIEW" in severities:
        return "review"
    return "ready"


def _blocking_reasons(findings: list) -> tuple[str, ...]:
    reasons = []
    for finding in findings:
        if finding.severity != "BLOCK":
            continue
        reasons.append(
            f"{finding.consumer_id}: {finding.message} "
            f"[{finding.rule_id}; {finding.change_id}]"
        )
    return tuple(sorted(set(reasons)))


def _pending(case: CaseData, pair: str, read: bool, write: bool) -> tuple[str, ...]:
    positions = {version.version: index for index, version in enumerate(case.versions)}
    from_version = pair.split("->", 1)[0]
    from_index = positions[from_version]
    selected = []
    for consumer in case.consumers:
        anchor = max(positions[version] for version in consumer.declared_versions)
        if anchor > from_index:
            continue
        if read and consumer.read_versions or write and consumer.write_versions:
            selected.append(consumer.consumer_id)
    return tuple(sorted(set(selected)))


def build_plan(
    case: CaseData,
    changes: tuple[Change, ...],
    verdicts: tuple[ConsumerVerdict, ...],
) -> tuple[tuple[MigrationStep, ...], tuple[RollbackHint, ...]]:
    pairs = [
        f"{case.versions[index].version}->{case.versions[index + 1].version}"
        for index in range(len(case.versions) - 1)
    ]
    steps: list[MigrationStep] = []
    pair_step_numbers: dict[str, int] = {}
    previous = None
    for pair in pairs:
        from_version, to_version = pair.split("->", 1)
        read_consumers = _pending(case, pair, read=True, write=False)
        read_findings = _findings_for_pair(verdicts, pair, read_consumers)
        number = len(steps) + 1
        pair_step_numbers[pair] = number
        prereq = ("input fingerprint pinned",) if previous is None else (f"step {previous} completed",)
        steps.append(
            MigrationStep(
                number=number,
                target_version=to_version,
                action=f"Publish declared schema {to_version} for readers of {from_version}",
                affected_consumers=read_consumers,
                prerequisites=prereq,
                reversible=True,
                status=_status(read_findings),
                blocking_reasons=_blocking_reasons(read_findings),
            )
        )
        previous = number
        write_consumers = _pending(case, pair, read=False, write=True)
        if write_consumers:
            write_findings = _findings_for_pair(verdicts, pair, write_consumers)
            number = len(steps) + 1
            steps.append(
                MigrationStep(
                    number=number,
                    target_version=to_version,
                    action=f"Enable writer cutover to {to_version}",
                    affected_consumers=write_consumers,
                    prerequisites=(f"step {previous} completed",),
                    reversible=True,
                    status=_status(write_findings),
                    blocking_reasons=_blocking_reasons(write_findings),
                )
            )
            previous = number
        pair_step_numbers[pair] = number
    if len(case.versions) > 1:
        all_consumers = tuple(sorted(consumer.consumer_id for consumer in case.consumers))
        all_findings = [
            finding for verdict in verdicts for finding in verdict.findings
            if finding.severity in {"BLOCK", "REVIEW"}
        ]
        final_number = len(steps) + 1
        steps.append(
            MigrationStep(
                number=final_number,
                target_version=case.versions[-1].version,
                action="Retire compatibility declarations for older versions after sign-off",
                affected_consumers=all_consumers,
                prerequisites=(f"step {previous} completed",) if previous else (),
                reversible=False,
                status=_status(all_findings),
                blocking_reasons=_blocking_reasons(all_findings),
            )
        )
        previous_final = final_number
    else:
        previous_final = previous
    identity = build_field_identity(case, changes)
    hints = []
    for pair in pairs:
        from_version = pair.split("->", 1)[0]
        pair_findings = [
            finding for finding in (item for verdict in verdicts for item in verdict.findings)
            if finding.pair_id == pair and finding.severity in {"BLOCK", "REVIEW"}
        ]
        pair_findings.sort(key=lambda item: (-SEVERITY_RANK[item.severity], item.consumer_id))
        consumers = tuple(sorted({finding.consumer_id for finding in pair_findings}))
        plan = rollback_retain_plan(case, from_version, consumers, identity)
        hints.append(
            RollbackHint(
                for_step=pair_step_numbers[pair],
                version_pair=pair,
                rollback_to=from_version,
                retain_fields=tuple(plan["retain_fields"]),
                consumers_to_rollback=consumers,
                note=(
                    "Keep old and new field names side by side until consumers move; "
                    "this tool emits no DDL and executes no rollback."
                ),
                details={
                    "rollback_point": plan["rollback_point"],
                    "consumer_versions": plan["consumer_versions"],
                    "reuse_across_rollback": plan["reuse_across_rollback"],
                },
            )
        )
    if previous_final is not None and len(case.versions) > 1:
        review_consumers = tuple(sorted({
            verdict.consumer_id for verdict in verdicts if verdict.decision != "PASS"
        }))
        reuse_entries = tuple(
            entry
            for hint in hints
            for entry in hint.details["reuse_across_rollback"]
        )
        final_version = case.versions[-1].version
        hints.append(
            RollbackHint(
                for_step=previous_final,
                version_pair="all",
                rollback_to=final_version,
                retain_fields=tuple(sorted({
                    field for hint in hints for field in hint.retain_fields
                })),
                consumers_to_rollback=review_consumers,
                note=(
                    "Re-enable the prior compatibility declarations before retiring them; "
                    "no external catalog or migration command is invoked."
                ),
                details={
                    "rollback_point": final_version,
                    "consumer_versions": consumer_rollback_versions(
                        case, review_consumers, final_version
                    ),
                    "reuse_across_rollback": merge_reuse_annotations(reuse_entries),
                },
            )
        )
    return tuple(steps), tuple(hints)
