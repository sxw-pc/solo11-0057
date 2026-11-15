"""Deterministic Markdown report rendering."""

from __future__ import annotations

from typing import Any


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item) for item in value) if value else "-"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text if text else "-"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    if not rows:
        rows = [["-"] * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return lines


def _partition_label(partition: dict[str, Any] | None) -> str:
    if not partition or not partition.get("partition_keys"):
        return "unpartitioned"
    keys = []
    for key in partition["partition_keys"]:
        transform = key["transform"]
        if transform == "bucket":
            transform = f"bucket({key.get('bucket_count')})"
        elif transform == "truncate":
            transform = f"truncate({key.get('truncate_width')})"
        keys.append(f"{key['column']}:{transform}")
    return ", ".join(keys)


def _finding_rows(findings: list[dict[str, Any]]) -> list[list[Any]]:
    return [
        [
            item["consumer_id"], item["severity"], item["rule_id"],
            item["change_id"], item["field_path"] or "-",
            item["evidence_path"], item["message"],
        ]
        for item in findings
    ]


def render_markdown_report(result: dict[str, Any]) -> str:
    case = result["case"]
    lines: list[str] = [
        f"# Schema evolution gate report: {case['case_id']}",
        "",
        f"- Case: `{case['case_id']}`",
        f"- Table: `{case['table']}`",
        f"- Description: {case.get('description') or '-'}",
        f"- Overall decision: **{result['decision']}**",
        f"- Input fingerprint: `{result['input_fingerprint']}`",
        f"- Tool version: `{result['tool_version']}`",
        f"- Rules version: `{result['rules_version']}`",
        f"- Report schema version: `{result['report_schema_version']}`",
        "",
        "## Versions",
        "",
    ]
    version_rows = [
        [
            version["version"], version["field_count"],
            f"`{version['schema_fingerprint']}`",
            _partition_label(version.get("partition_spec")),
        ]
        for version in result["versions"]
    ]
    lines.extend(_table(
        ["Version", "Fields", "Schema fingerprint", "Partition specification"],
        version_rows,
    ))
    lines.extend(["", "## Version pairs", ""])
    pair_rows = [
        [
            pair["pair_id"], pair["from_version"], pair["to_version"],
            pair["change_count"], "yes" if pair["partition_changed"] else "no",
        ]
        for pair in result["version_pairs"]
    ]
    lines.extend(_table(
        ["Pair", "From", "To", "Changes", "Partition changed"], pair_rows,
    ))
    lines.extend(["", "## Changes", ""])
    change_rows = [
        [
            change["pair_id"], change["change_type"], change["field_path"],
            change.get("evidence_version") or "-",
            change.get("evidence_direction") or "-",
            change["change_id"], change["evidence_path"], change["summary"],
        ]
        for change in result["changes"]
    ]
    lines.extend(_table(
        ["Pair", "Type", "Field path", "Evidence version", "Evidence direction",
         "Change ID", "Evidence", "Summary"],
        change_rows,
    ))
    lines.extend(["", "## Consumer compatibility matrix", ""])
    matrix = result["compatibility_matrix"]
    matrix_rows = []
    for row in matrix["rows"]:
        decisions = [row["decisions"].get(pair, "N/A") for pair in matrix["version_pairs"]]
        matrix_rows.append(
            [row["consumer_id"], row["mode"], row["anchor_version"], *decisions, row["overall"]]
        )
    lines.extend(_table(
        ["Consumer", "Mode", "Anchor", *matrix["version_pairs"], "Overall"],
        matrix_rows,
    ))
    lines.extend(["", "## Findings", ""])
    all_findings = [
        finding for consumer in result["consumers"] for finding in consumer["findings"]
    ]
    lines.extend(_table(
        ["Consumer", "Severity", "Rule", "Change ID", "Field path", "Evidence", "Message"],
        _finding_rows(all_findings),
    ))
    for title, key in (("Blocking items", "blocked_items"), ("Review items", "review_items")):
        lines.extend(["", f"## {title}", ""])
        lines.extend(_table(
            ["Consumer", "Severity", "Rule", "Change ID", "Field path", "Evidence", "Message"],
            _finding_rows(result[key]),
        ))
    lines.extend(["", "## Migration steps (advisory only)", ""])
    step_rows = [
        [
            step["number"], step["target_version"], step["action"],
            ", ".join(step["affected_consumers"]) or "-",
            "; ".join(step["prerequisites"]) or "-",
            "yes" if step["reversible"] else "no",
            step["status"],
            "; ".join(step["blocking_reasons"]) or "-",
        ]
        for step in result["migration_steps"]
    ]
    lines.extend(_table(
        ["#", "Target", "Action", "Affected consumers", "Prerequisites",
         "Reversible", "Status", "Blocking reasons"],
        step_rows,
    ))
    lines.extend(["", "## Rollback hints", ""])
    hint_rows = [
        [
            hint["for_step"], hint["version_pair"], hint["rollback_to"],
            ", ".join(hint["retain_fields"]) or "-",
            ", ".join(hint["consumers_to_rollback"]) or "-", hint["note"],
        ]
        for hint in result["rollback_hints"]
    ]
    lines.extend(_table(
        ["For step", "Pair", "Roll back to", "Retain fields",
         "Consumers to roll back first", "Note"],
        hint_rows,
    ))
    lines.extend([
        "",
        "## Policy",
        "",
        f"- Policy ID: `{result['policy']['policy_id']}`",
        f"- Default mode: `{result['policy']['default_mode']}`",
        "- Forbidden changes: " + (", ".join(
            f"`{item}`" for item in result["policy"]["forbidden_changes"]
        ) or "-"),
        "- Review changes: " + (", ".join(
            f"`{item}`" for item in result["policy"]["review_changes"]
        ) or "-"),
        "",
        "## Scope notice",
        "",
        result["disclaimer"],
        "",
    ])
    return "\n".join(lines)
