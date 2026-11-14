"""Flask application factory for the local SchemaGate service."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, current_app, render_template, request

from .api import api, healthz
from .canonical import canonical_json
from .config import (
    DEFAULT_DB_PATH,
    DEFAULT_FIXTURES_PATH,
    DEFAULT_REPORTS_PATH,
)
from .errors import SchemaGateError
from .ledger import Ledger
from .loader import CaseLoader
from .runner import RunService

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _resolve_path(value: str | Path | None, env_name: str, default: Path) -> Path:
    raw = value if value is not None else os.environ.get(env_name)
    if raw is None:
        return default
    path = Path(raw)
    return path if path.is_absolute() else (Path.cwd() / path)


def _status_for_error(code: str) -> int:
    if code in {"SG-API-001", "SG-API-004", "SG-API-006", "SG-LOAD-013"}:
        return 400
    if code in {"SG-API-002", "SG-API-003", "SG-LOAD-007"}:
        return 404
    if code == "SG-LEDGER-001":
        return 409
    if code.startswith("SG-LOAD-"):
        return 422
    if code.startswith("SG-LEDGER-"):
        return 500
    return 500


def create_app(
    fixtures_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    reports_dir: str | Path | None = None,
    testing: bool = False,
) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(TESTING=testing)
    resolved_fixtures = _resolve_path(
        fixtures_dir, "SCHEMAGATE_FIXTURES", _REPO_ROOT / DEFAULT_FIXTURES_PATH
    )
    resolved_db = _resolve_path(db_path, "SCHEMAGATE_DB", _REPO_ROOT / DEFAULT_DB_PATH)
    resolved_reports = _resolve_path(
        reports_dir, "SCHEMAGATE_REPORTS", _REPO_ROOT / DEFAULT_REPORTS_PATH
    )
    loader = CaseLoader(resolved_fixtures)
    ledger = Ledger(resolved_db)
    service = RunService(loader, ledger, resolved_reports)
    app.extensions["schemagate"] = service
    app.config["SCHEMAGATE_FIXTURES"] = str(resolved_fixtures)
    app.config["SCHEMAGATE_DB"] = str(resolved_db)
    app.config["SCHEMAGATE_REPORTS"] = str(resolved_reports)
    app.register_blueprint(api)
    app.register_blueprint(healthz)
    @app.get("/")
    def index():
        runs = service.recent_runs(limit=10)
        return render_template("index.html", cases=service.list_cases(), runs=runs)
    @app.errorhandler(SchemaGateError)
    def handle_schema_gate_error(error: SchemaGateError):
        if request.path.startswith("/api/"):
            payload = canonical_json({"error": error.to_dict()}) + "\n"
            return current_app.response_class(
                payload,
                status=_status_for_error(error.code),
                mimetype="application/json",
            )
        return f"{error.code}: {error.message}", _status_for_error(error.code)
    @app.errorhandler(404)
    def handle_not_found(error):
        if request.path.startswith("/api/"):
            payload = canonical_json({
                "error": {
                    "code": "SG-API-005",
                    "message": "endpoint not found",
                    "details": {},
                }
            }) + "\n"
            return current_app.response_class(
                payload, status=404, mimetype="application/json"
            )
        return "Not found", 404
    return app
