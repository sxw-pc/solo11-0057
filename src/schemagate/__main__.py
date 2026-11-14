"""Command-line entry point used only to start the local HTTP service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import create_app
from .config import (
    DEFAULT_DB_PATH,
    DEFAULT_FIXTURES_PATH,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_REPORTS_PATH,
    TOOL_VERSION,
    validate_bind_host,
    validate_port,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schemagate",
        description="Local, offline schema evolution admission service.",
    )
    parser.add_argument("--version", action="version", version=f"schemagate {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="start the loopback HTTP service")
    serve.add_argument("--host", default=DEFAULT_HOST, help="loopback bind host")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="bind port")
    serve.add_argument(
        "--fixtures",
        default=str(_REPO_ROOT / DEFAULT_FIXTURES_PATH),
        help="case fixture directory",
    )
    serve.add_argument(
        "--db",
        default=str(_REPO_ROOT / DEFAULT_DB_PATH),
        help="SQLite ledger path",
    )
    serve.add_argument(
        "--reports",
        default=str(_REPO_ROOT / DEFAULT_REPORTS_PATH),
        help="report output directory",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command != "serve":
        parser.print_help()
        return 2
    try:
        host = validate_bind_host(args.host)
        port = validate_port(args.port)
    except ValueError as error:
        print(f"SG-CONFIG-001: {error}", file=sys.stderr)
        return 2
    app = create_app(
        fixtures_dir=args.fixtures,
        db_path=args.db,
        reports_dir=args.reports,
    )
    print(f"SchemaGate {TOOL_VERSION} serving on {host}:{port} (loopback only)")
    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
