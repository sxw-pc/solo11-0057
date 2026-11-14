"""SchemaGate's stable, machine-readable error type."""

from __future__ import annotations

from typing import Any


class SchemaGateError(Exception):
    """An error with a stable code and a safe, local-only message."""
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        **extra_details: Any,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        merged = dict(details or {})
        merged.update(extra_details)
        self.details = merged
    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def fail(code: str, message: str, **details: Any) -> None:
    """Raise a SchemaGateError with keyword details."""
    raise SchemaGateError(code, message, details)
