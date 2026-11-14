"""Advisory migration ordering and rollback hints; nothing is executed."""

from __future__ import annotations

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


def _retain_fields(pair_changes: tuple[Change, ...]) -> tuple[str, ...]:
    retained = set()
    for change in pair_changes:
        if change.change_type == "drop_field":
            retained.add(change.field_path)
        elif change.change_type == "rename_field":
            retained.add(str(change.details.get("old_path", change.field_path)))
            retained.add(str(change.details.get("new_path", change.field_path)))
    return tuple(sorted(retained))


def build_plan(
    case: CaseData,
    changes: tuple[Change, ...],
    verdicts: tuple[ConsumerVerdict, ...],
) -> tuple[tuple[MigrationStep, ...], tuple[RollbackHint, ...]]:
    pairs = [
        f"{case.versions[index].version}->{case.versions[index + 1].version}"
        for index in range(len(case.versions) - 1)
    ]
    changes_by_pair = {pair: [] for pair in pairs}
    for change in changes:
        changes_by_pair.setdefault(change.pair_id, []).append(change)
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
    hints = []
    for pair in pairs:
        from_version = pair.split("->", 1)[0]
        pair_findings = [
            finding for finding in (item for verdict in verdicts for item in verdict.findings)
            if finding.pair_id == pair and finding.severity in {"BLOCK", "REVIEW"}
        ]
        pair_findings.sort(key=lambda item: (-SEVERITY_RANK[item.severity], item.consumer_id))
        consumers = tuple(sorted({finding.consumer_id for finding in pair_findings}))
        hints.append(
            RollbackHint(
                for_step=pair_step_numbers[pair],
                version_pair=pair,
                rollback_to=from_version,
                retain_fields=_retain_fields(tuple(changes_by_pair[pair])),
                consumers_to_rollback=consumers,
                note=(
                    "Keep old and new field names side by side until consumers move; "
                    "this tool emits no DDL and executes no rollback."
                ),
            )
        )
    if previous_final is not None and len(case.versions) > 1:
        review_consumers = tuple(sorted({
            verdict.consumer_id for verdict in verdicts if verdict.decision != "PASS"
        }))
        hints.append(
            RollbackHint(
                for_step=previous_final,
                version_pair="all",
                rollback_to=case.versions[-1].version,
                retain_fields=tuple(sorted({
                    field for hint in hints for field in hint.retain_fields
                })),
                consumers_to_rollback=review_consumers,
                note=(
                    "Re-enable the prior compatibility declarations before retiring them; "
                    "no external catalog or migration command is invoked."
                ),
            )
        )
    return tuple(steps), tuple(hints)
