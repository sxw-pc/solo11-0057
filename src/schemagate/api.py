"""Local JSON API and health endpoint."""

from __future__ import annotations

import re

from flask import Blueprint, current_app, request

from .canonical import canonical_json
from .config import REPORT_FORMATS
from .errors import SchemaGateError
from .runner import RunService

api = Blueprint("api", __name__, url_prefix="/api/v1")
healthz = Blueprint("healthz", __name__)

_LIMIT_RE = re.compile(r"^\d{1,3}$")


def _service() -> RunService:
    return current_app.extensions["schemagate"]


def _json(payload: object, status: int = 200, mimetype: str = "application/json"):
    return current_app.response_class(
        canonical_json(payload) + "\n", status=status, mimetype=mimetype
    )


def _translate_unknown_case(error: SchemaGateError) -> SchemaGateError:
    if error.code == "SG-LOAD-007":
        return SchemaGateError("SG-API-002", "unknown case_id", error.details)
    return error


@healthz.get("/healthz")
def health():
    return _json({"status": "ok"})


@api.get("/cases")
def list_cases():
    items = _service().list_cases()
    return _json({"items": items, "count": len(items)})


@api.post("/runs")
def create_run():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise SchemaGateError("SG-API-001", "request body must be a JSON object")
    case_id = data.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise SchemaGateError("SG-API-001", "case_id must be a non-empty string")
    try:
        outcome = _service().execute(case_id.strip())
    except SchemaGateError as error:
        translated = _translate_unknown_case(error)
        if translated is error:
            raise
        raise translated from error
    return _json({
        "run_id": outcome.run_id,
        "case_id": case_id.strip(),
        "decision": outcome.decision,
        "summary": outcome.summary,
        "reused": outcome.reused,
        "attempt_count": outcome.attempt_count,
        "links": {
            "self": f"/api/v1/runs/{outcome.run_id}",
            "report_json": f"/api/v1/runs/{outcome.run_id}/report?format=json",
            "report_markdown": f"/api/v1/runs/{outcome.run_id}/report?format=markdown",
            "report_html": f"/api/v1/runs/{outcome.run_id}/report?format=html",
        },
    })


@api.get("/runs/<run_id>")
def get_run(run_id: str):
    content = _service().get_report(run_id, "json")
    return current_app.response_class(
        content, status=200, mimetype="application/json"
    )


@api.get("/runs/<run_id>/report")
def get_report(run_id: str):
    report_format = (request.args.get("format") or "json").strip().lower()
    if report_format not in REPORT_FORMATS:
        raise SchemaGateError(
            "SG-API-004", "format must be json, markdown, or html",
            {"format": report_format},
        )
    content = _service().get_report(run_id, report_format)
    mimetypes = {
        "json": "application/json",
        "markdown": "text/markdown",
        "html": "text/html",
    }
    return current_app.response_class(
        content, status=200, mimetype=mimetypes[report_format]
    )


@api.get("/ledger")
def ledger_list():
    service = _service()
    case_id = request.args.get("case_id")
    if case_id is not None and (not case_id or case_id not in service.loader.case_ids()):
        raise SchemaGateError("SG-API-002", "unknown case_id", {"case_id": case_id})
    raw_limit = request.args.get("limit", "20")
    if not _LIMIT_RE.match(raw_limit):
        raise SchemaGateError("SG-API-006", "limit must be an integer between 1 and 200")
    limit = int(raw_limit)
    if not 1 <= limit <= 200:
        raise SchemaGateError("SG-API-006", "limit must be an integer between 1 and 200")
    items = service.ledger.list_runs(case_id=case_id, limit=limit)
    return _json({"items": items, "count": len(items)})
