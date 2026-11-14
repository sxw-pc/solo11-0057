"""SQLite decision ledger with deterministic, idempotent run records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .canonical import canonical_json
from .errors import SchemaGateError

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY, table_name TEXT NOT NULL,
    description TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(case_id),
    table_name TEXT NOT NULL, input_fingerprint TEXT NOT NULL,
    tool_version TEXT NOT NULL, rules_version TEXT NOT NULL,
    report_schema_version INTEGER NOT NULL, decision TEXT NOT NULL,
    summary_json TEXT NOT NULL, result_json TEXT NOT NULL,
    report_json_path TEXT, report_markdown_path TEXT, report_html_path TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    UNIQUE(case_id, input_fingerprint, tool_version, rules_version)
);
CREATE TABLE IF NOT EXISTS version_pairs (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    pair_id TEXT NOT NULL, from_version TEXT NOT NULL, to_version TEXT NOT NULL,
    change_count INTEGER NOT NULL, PRIMARY KEY (run_id, pair_id)
);
CREATE TABLE IF NOT EXISTS changes (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    change_id TEXT NOT NULL, pair_id TEXT NOT NULL, change_type TEXT NOT NULL,
    field_path TEXT NOT NULL, evidence_path TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY (run_id, change_id)
);
CREATE TABLE IF NOT EXISTS consumer_verdicts (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    consumer_id TEXT NOT NULL, decision TEXT NOT NULL, mode TEXT NOT NULL,
    anchor_version TEXT NOT NULL, finding_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY (run_id, consumer_id)
);
CREATE TABLE IF NOT EXISTS findings (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    consumer_id TEXT NOT NULL, change_id TEXT NOT NULL, severity TEXT NOT NULL,
    rule_id TEXT NOT NULL, evidence_path TEXT NOT NULL, pair_id TEXT NOT NULL,
    payload_json TEXT NOT NULL, PRIMARY KEY (run_id, consumer_id, change_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_case_seen ON runs(case_id, last_seen_at);
CREATE INDEX IF NOT EXISTS idx_findings_run_severity ON findings(run_id, severity);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Ledger:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        if not str(self.db_path).strip():
            raise SchemaGateError("SG-LEDGER-002", "ledger path must not be empty")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.commit()
    def register_run(
        self,
        result: dict[str, Any],
        report_paths: dict[str, str],
    ) -> tuple[bool, int, dict[str, Any]]:
        """Insert or reuse a run. Returns (reused, attempt_count, stored_result)."""
        run_id = str(result["run_id"])
        case = result["case"]
        case_id = str(case["case_id"])
        table = str(case["table"])
        fingerprint = str(result["input_fingerprint"])
        now = _now()
        serialized = canonical_json(result)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO cases(case_id, table_name, description, first_seen_at, last_seen_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    table_name = excluded.table_name,
                    description = excluded.description,
                    last_seen_at = excluded.last_seen_at
                """,
                (case_id, table, str(case.get("description", "")), now, now),
            )
            existing = connection.execute(
                """
                SELECT * FROM runs WHERE run_id = ?
                   OR (case_id = ? AND input_fingerprint = ?
                       AND tool_version = ? AND rules_version = ?)
                """,
                (
                    run_id, case_id, fingerprint,
                    result["tool_version"], result["rules_version"],
                ),
            ).fetchone()
            if existing is not None:
                if existing["result_json"] != serialized:
                    raise SchemaGateError(
                        "SG-LEDGER-001",
                        "idempotency conflict: stored result differs for the same fingerprint",
                        {"run_id": existing["run_id"]},
                    )
                connection.execute(
                    """
                    UPDATE runs SET attempt_count = attempt_count + 1,
                        last_seen_at = ?,
                        report_json_path = ?, report_markdown_path = ?, report_html_path = ?
                    WHERE run_id = ?
                    """,
                    (
                        now, report_paths.get("json"), report_paths.get("markdown"),
                        report_paths.get("html"), existing["run_id"],
                    ),
                )
                stored = json.loads(existing["result_json"])
                return True, int(existing["attempt_count"]) + 1, stored
            connection.execute(
                """
                INSERT INTO runs(
                    run_id, case_id, table_name, input_fingerprint, tool_version,
                    rules_version, report_schema_version, decision, summary_json,
                    result_json, report_json_path, report_markdown_path,
                    report_html_path, attempt_count, created_at, last_seen_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, case_id, table, fingerprint, result["tool_version"],
                    result["rules_version"], int(result["report_schema_version"]),
                    result["decision"], canonical_json(result["summary"]), serialized,
                    report_paths.get("json"), report_paths.get("markdown"),
                    report_paths.get("html"), 1, now, now,
                ),
            )
            self._insert_details(connection, run_id, result)
            return False, 1, result
    def _insert_details(
        self, connection: sqlite3.Connection, run_id: str, result: dict[str, Any]
    ) -> None:
        for pair in result.get("version_pairs", []):
            connection.execute(
                """
                INSERT INTO version_pairs(run_id, pair_id, from_version, to_version, change_count)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    run_id, pair["pair_id"], pair["from_version"], pair["to_version"],
                    int(pair["change_count"]),
                ),
            )
        for change in result.get("changes", []):
            connection.execute(
                """
                INSERT INTO changes(
                    run_id, change_id, pair_id, change_type, field_path,
                    evidence_path, payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, change["change_id"], change["pair_id"], change["change_type"],
                    change["field_path"], change["evidence_path"], canonical_json(change),
                ),
            )
        for verdict in result.get("consumers", []):
            connection.execute(
                """
                INSERT INTO consumer_verdicts(
                    run_id, consumer_id, decision, mode, anchor_version,
                    finding_count, payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, verdict["consumer_id"], verdict["decision"], verdict["mode"],
                    verdict["anchor_version"], len(verdict.get("findings", [])),
                    canonical_json(verdict),
                ),
            )
            for finding in verdict.get("findings", []):
                connection.execute(
                    """
                    INSERT OR REPLACE INTO findings(
                        run_id, consumer_id, change_id, severity, rule_id,
                        evidence_path, pair_id, payload_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, finding["consumer_id"], finding["change_id"],
                        finding["severity"], finding["rule_id"], finding["evidence_path"],
                        finding["pair_id"], canonical_json(finding),
                    ),
                )
    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])
    def get_run_metadata(self, run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._run_row(row) if row is not None else None
    def list_runs(self, case_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise SchemaGateError("SG-LEDGER-003", "limit must be between 1 and 200")
        query = "SELECT * FROM runs"
        parameters: list[Any] = []
        if case_id is not None:
            query += " WHERE case_id = ?"
            parameters.append(case_id)
        query += " ORDER BY last_seen_at DESC, run_id ASC LIMIT ?"
        parameters.append(limit)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._run_row(row) for row in rows]
    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "case_id": row["case_id"],
            "table": row["table_name"],
            "input_fingerprint": row["input_fingerprint"],
            "tool_version": row["tool_version"],
            "rules_version": row["rules_version"],
            "decision": row["decision"],
            "summary": json.loads(row["summary_json"]),
            "attempt_count": int(row["attempt_count"]),
            "created_at": row["created_at"],
            "last_seen_at": row["last_seen_at"],
            "report_paths": {
                "json": row["report_json_path"],
                "markdown": row["report_markdown_path"],
                "html": row["report_html_path"],
            },
        }
