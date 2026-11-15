"""Stable constants and small shared helpers for schemagate."""

from __future__ import annotations

TOOL_VERSION = "0.1.0"
RULES_VERSION = "2026.11.1"
REPORT_SCHEMA_VERSION = 2
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_DB_PATH = "var/schemagate.db"
DEFAULT_FIXTURES_PATH = "fixtures/cases"
DEFAULT_REPORTS_PATH = "var/reports"
COMPAT_MODES = ("backward", "forward", "full", "none")
SEVERITIES = ("PASS", "REVIEW", "BLOCK")
SEVERITY_RANK = {"PASS": 0, "REVIEW": 1, "BLOCK": 2}
CHANGE_TYPES = frozenset(
    "add_field drop_field rename_field widen_type narrow_type type_change "  # noqa: SIM905
    "nullable_to_true nullable_to_false default_added default_removed default_changed "
    "field_order_change struct_field_change element_type_change decimal_change "
    "timestamp_unit_change constraint_change partition_change".split()
)

KNOWN_TYPE_KINDS = frozenset({
    "boolean", "int32", "int64", "float32", "float64", "string",
    "binary", "date", "timestamp_ms", "timestamp_us", "decimal",
    "uuid", "struct", "list", "map",
})

PRIMITIVE_KINDS = frozenset({
    "boolean", "int32", "int64", "float32", "float64", "string",
    "binary", "date", "timestamp_ms", "timestamp_us", "decimal", "uuid",
})

WIDENING_PAIRS = frozenset({
    ("int32", "int64"),
    ("int32", "float64"),
    ("float32", "float64"),
})

PARTITION_TRANSFORMS = frozenset({"identity", "bucket", "truncate"})
REPORT_FORMATS = frozenset({"json", "markdown", "html"})


def is_loopback_host(host: str) -> bool:
    """Accept only explicit localhost spellings; never 0.0.0.0."""
    if host in {"127.0.0.1", "::1", "localhost"}:
        return True
    if host.startswith("127."):
        parts = host.split(".")
        return len(parts) == 4 and all(part.isdigit() for part in parts)
    return False


def validate_bind_host(host: str) -> str:
    """Reject wildcard and non-loopback bind addresses."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be a non-empty loopback address")
    normalized = host.strip().lower()
    if normalized == "0.0.0.0" or normalized == "::":
        raise ValueError("refusing to bind to a wildcard address; use 127.0.0.1")
    if not is_loopback_host(normalized):
        raise ValueError("refusing to bind to a non-loopback address; use 127.0.0.1")
    return normalized


def validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return port
