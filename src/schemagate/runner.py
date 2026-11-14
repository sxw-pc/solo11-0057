"""Run orchestration, report persistence, and HTTP-facing services."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .canonical import canonical_json
from .compatibility import evaluate_case
from .config import REPORT_SCHEMA_VERSION, RULES_VERSION, SEVERITY_RANK, TOOL_VERSION
from .diff_engine import diff_versions
from .errors import SchemaGateError
from .loader import CaseLoader
from .models import CaseData, Change, ConsumerVerdict, RunOutcome
from .planner import build_plan
from .report_html import render_html_report
from .report_json import render_json_report
from .report_markdown import render_markdown_report

_DISCLAIMER = (
    "SchemaGate is a local, offline, declarative admission check. It reads only "
    "the case fixtures, executes no DDL, opens no data files, and contacts no "
    "catalog or network service."
)


def _partition_dict(spec: Any) -> dict[str, Any] | None:
    if spec is None:
        return None
    return {
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


def _seq_dict(obj: Any, *names: str) -> dict[str, Any]:
    payload = asdict(obj)
    for name in names:
        payload[name] = list(payload[name])
    return payload


def compute_run_id(case: CaseData) -> str:
    seed = {
        "case_id": case.case_id,
        "input_fingerprint": case.input_fingerprint,
        "tool_version": TOOL_VERSION,
        "rules_version": RULES_VERSION,
        "report_schema_version": REPORT_SCHEMA_VERSION,
    }
    digest = hashlib.sha256(canonical_json(seed).encode("utf-8")).hexdigest()
    return f"run-{digest[:24]}"


def _collect_changes(case: CaseData) -> tuple[Change, ...]:
    changes: list[Change] = []
    for index in range(len(case.versions) - 1):
        changes.extend(diff_versions(case, case.versions[index], case.versions[index + 1]))
    return tuple(changes)


def _pair_rows(case: CaseData, changes: tuple[Change, ...]) -> list[dict[str, Any]]:
    rows = []
    for index in range(len(case.versions) - 1):
        old, new = case.versions[index], case.versions[index + 1]
        pair = f"{old.version}->{new.version}"
        pair_changes = [change for change in changes if change.pair_id == pair]
        rows.append({
            "pair_id": pair,
            "from_version": old.version,
            "to_version": new.version,
            "change_count": len(pair_changes),
            "change_ids": [change.change_id for change in pair_changes],
            "partition_changed": any(
                change.change_type == "partition_change" for change in pair_changes
            ),
        })
    return rows


def _matrix(
    case: CaseData,
    verdicts: tuple[ConsumerVerdict, ...],
    pairs: list[dict[str, Any]],
) -> dict[str, Any]:
    positions = {version.version: index for index, version in enumerate(case.versions)}
    pair_ids = [pair["pair_id"] for pair in pairs]
    rows = []
    for verdict in verdicts:
        anchor_index = positions[verdict.anchor_version]
        decisions = {}
        for pair in pairs:
            if positions[pair["from_version"]] < anchor_index:
                decisions[pair["pair_id"]] = "N/A"
                continue
            matched = [f for f in verdict.findings if f.pair_id == pair["pair_id"]]
            decisions[pair["pair_id"]] = max(
                (finding.severity for finding in matched),
                key=lambda item: SEVERITY_RANK[item], default="PASS",
            )
        rows.append({
            "consumer_id": verdict.consumer_id,
            "mode": verdict.mode,
            "anchor_version": verdict.anchor_version,
            "overall": verdict.decision,
            "decisions": {pair_id: decisions[pair_id] for pair_id in pair_ids},
        })
    return {"version_pairs": pair_ids, "rows": rows}


def build_result(case: CaseData) -> dict[str, Any]:
    changes = _collect_changes(case)
    verdicts = evaluate_case(case, changes)
    steps, hints = build_plan(case, changes, verdicts)
    pairs = _pair_rows(case, changes)
    decision = max((item.decision for item in verdicts),
                   key=lambda value: SEVERITY_RANK[value], default="PASS")
    findings = [finding for verdict in verdicts for finding in verdict.findings]
    counts = {severity: sum(f.severity == severity for f in findings)
              for severity in ("PASS", "REVIEW", "BLOCK")}
    consumer_counts = {severity: sum(v.decision == severity for v in verdicts)
                       for severity in ("PASS", "REVIEW", "BLOCK")}
    change_map = {change.change_id: asdict(change) for change in changes}
    buckets: dict[str, list[dict[str, Any]]] = {"BLOCK": [], "REVIEW": []}
    for finding in findings:
        if finding.severity in buckets:
            payload = asdict(finding)
            payload["change_summary"] = change_map.get(finding.change_id, {}).get("summary", "")
            buckets[finding.severity].append(payload)
    for items in buckets.values():
        items.sort(key=lambda item: (item["consumer_id"], item["change_id"]))
    result: dict[str, Any] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "rules_version": RULES_VERSION,
        "run_id": "",
        "decision": decision,
        "case": {"case_id": case.case_id, "table": case.table,
                 "description": case.description},
        "input_fingerprint": case.input_fingerprint,
        "versions": [
            {
                "version": version.version,
                "schema_fingerprint": version.schema_fingerprint,
                "field_count": len(version.fields),
                "partition_spec": _partition_dict(case.partitions.get(version.version)),
            }
            for version in case.versions
        ],
        "version_pairs": pairs,
        "changes": [asdict(change) for change in changes],
        "policy": {
            "policy_id": case.policy.policy_id,
            "default_mode": case.policy.default_mode,
            "forbidden_changes": list(case.policy.forbidden_changes),
            "review_changes": list(case.policy.review_changes),
        },
        "consumers": [
            {
                "consumer_id": verdict.consumer_id,
                "mode": verdict.mode,
                "anchor_version": verdict.anchor_version,
                "read_versions": list(verdict.read_versions),
                "write_versions": list(verdict.write_versions),
                "decision": verdict.decision,
                "findings": [asdict(finding) for finding in verdict.findings],
            }
            for verdict in verdicts
        ],
        "compatibility_matrix": _matrix(case, verdicts, pairs),
        "summary": {
            "change_count": len(changes),
            "version_pair_count": len(pairs),
            "consumer_count": len(verdicts),
            "finding_count": len(findings),
            "pass_count": counts["PASS"],
            "review_count": counts["REVIEW"],
            "block_count": counts["BLOCK"],
            "consumer_decisions": consumer_counts,
        },
        "blocked_items": buckets["BLOCK"],
        "review_items": buckets["REVIEW"],
        "migration_steps": [
            _seq_dict(step, "affected_consumers", "prerequisites", "blocking_reasons")
            for step in steps
        ],
        "rollback_hints": [
            _seq_dict(hint, "retain_fields", "consumers_to_rollback") for hint in hints
        ],
        "disclaimer": _DISCLAIMER,
    }
    result["run_id"] = compute_run_id(case)
    return result


class RunService:
    def __init__(self, loader: CaseLoader, ledger: Any, reports_dir: str | Path):
        self.loader = loader
        self.ledger = ledger
        self.reports_dir = Path(reports_dir).resolve()
        self.reports_dir.mkdir(parents=True, exist_ok=True)
    def list_cases(self) -> list[dict[str, Any]]:
        return self.loader.list_cases()
    def _write_reports(self, result: dict[str, Any]) -> dict[str, str]:
        run_id = result["run_id"]
        paths = {
            "json": self.reports_dir / f"{run_id}.json",
            "markdown": self.reports_dir / f"{run_id}.md",
            "html": self.reports_dir / f"{run_id}.html",
        }
        content = {
            "json": render_json_report(result),
            "markdown": render_markdown_report(result),
            "html": render_html_report(result),
        }
        for key, path in paths.items():
            with path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(content[key])
        return {key: str(path) for key, path in paths.items()}
    def execute(self, case_id: str) -> RunOutcome:
        result = build_result(self.loader.load_case(case_id))
        reused, attempts, stored = self.ledger.register_run(
            result, self._write_reports(result)
        )
        return RunOutcome(
            run_id=stored["run_id"], decision=stored["decision"],
            summary=stored["summary"], reused=reused,
            attempt_count=attempts, result=stored,
        )
    def get_run(self, run_id: str) -> dict[str, Any]:
        result = self.ledger.get_run(run_id)
        if result is None:
            raise SchemaGateError("SG-API-003", "unknown run_id", run_id=run_id)
        return result
    def get_report(self, run_id: str, report_format: str) -> str:
        result = self.get_run(run_id)
        renderers = {
            "json": render_json_report,
            "markdown": render_markdown_report,
            "html": render_html_report,
        }
        if report_format not in renderers:
            raise SchemaGateError(
                "SG-API-004", "unsupported report format", format=report_format
            )
        return renderers[report_format](result)
    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.ledger.list_runs(limit=limit)
