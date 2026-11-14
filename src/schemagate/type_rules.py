"""Schema type grammar, normalization, and widening classification."""

from __future__ import annotations

import re
from typing import Any

from .config import KNOWN_TYPE_KINDS, PRIMITIVE_KINDS, WIDENING_PAIRS
from .errors import SchemaGateError
from .models import FieldSpec, TypeSpec

_PRIMITIVE_ALIASES = {
    "bool": "boolean",
    "boolean": "boolean",
    "int": "int32",
    "integer": "int32",
    "int32": "int32",
    "long": "int64",
    "bigint": "int64",
    "int64": "int64",
    "float": "float32",
    "real": "float32",
    "float32": "float32",
    "double": "float64",
    "float64": "float64",
    "str": "string",
    "string": "string",
    "varchar": "string",
    "text": "string",
    "binary": "binary",
    "bytes": "binary",
    "date": "date",
    "timestamp": "timestamp_ms",
    "timestamp_ms": "timestamp_ms",
    "timestamp_millis": "timestamp_ms",
    "timestamp_us": "timestamp_us",
    "timestamp_micros": "timestamp_us",
    "uuid": "uuid",
}

_DECIMAL_RE = re.compile(r"^decimal\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)$")
_GENERIC_RE = re.compile(r"^(list|map)\s*<(.+)>$", re.DOTALL)


def invalid_type(expression: Any, reason: str) -> SchemaGateError:
    return SchemaGateError(
        "SG-LOAD-003",
        f"invalid schema type: {reason}",
        {"type": expression},
    )


def _decimal(expression: str) -> TypeSpec:
    match = _DECIMAL_RE.match(expression)
    assert match is not None
    precision, scale = int(match.group(1)), int(match.group(2))
    if not 1 <= precision <= 38:
        raise invalid_type(expression, "decimal precision must be between 1 and 38")
    if not 0 <= scale <= precision:
        raise invalid_type(expression, "decimal scale must be between 0 and precision")
    return TypeSpec("decimal", precision=precision, scale=scale)


def _split_top_level(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(body):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth < 0:
                raise invalid_type(body, "unbalanced angle brackets")
        elif char == "," and depth == 0:
            parts.append(body[start:index].strip())
            start = index + 1
    if depth != 0:
        raise invalid_type(body, "unbalanced angle brackets")
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def parse_type(expression: Any) -> TypeSpec:
    """Parse a scalar or generic list/map type string.
    Struct fields are declared with a literal ``type: struct`` plus a
    ``fields`` list, because struct members need field metadata.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise invalid_type(expression, "type must be a non-empty string")
    text = " ".join(expression.strip().lower().split())
    if text in _PRIMITIVE_ALIASES:
        return TypeSpec(_PRIMITIVE_ALIASES[text])
    if text == "decimal" or text.startswith("decimal("):
        return _decimal(text)
    generic = _GENERIC_RE.match(text)
    if generic:
        kind, body = generic.group(1), generic.group(2).strip()
        parts = _split_top_level(body)
        if kind == "list":
            if len(parts) != 1:
                raise invalid_type(expression, "list<> requires exactly one element type")
            return TypeSpec("list", element=parse_type(parts[0]))
        if len(parts) != 2:
            raise invalid_type(expression, "map<> requires key and value types")
        key_type, value_type = parse_type(parts[0]), parse_type(parts[1])
        if key_type.kind not in PRIMITIVE_KINDS:
            raise invalid_type(expression, "map key type must be a primitive type")
        return TypeSpec("map", key=key_type, value=value_type)
    if text.startswith("struct"):
        raise invalid_type(
            expression,
            "struct members must be declared with type: struct and fields: [...]",
        )
    raise invalid_type(expression, "unrecognized type name")


def type_repr(spec: TypeSpec, children: tuple[FieldSpec, ...] = ()) -> str:
    """Build a canonical, readable type representation."""
    if spec.kind == "decimal":
        return f"decimal({spec.precision},{spec.scale})"
    if spec.kind == "list":
        assert spec.element is not None
        return f"list<{type_signature(spec.element)}>"
    if spec.kind == "map":
        assert spec.key is not None and spec.value is not None
        return f"map<{type_signature(spec.key)},{type_signature(spec.value)}>"
    if spec.kind == "struct":
        inner = ",".join(
            f"{child.name}:{field_type_signature(child)}"
            for child in sorted(children, key=lambda item: item.name)
        )
        return f"struct<{inner}>"
    return spec.kind


def type_signature(spec: TypeSpec) -> str:
    """Signature for nested list/map type descriptors."""
    return type_repr(spec)


def field_type_signature(field: FieldSpec) -> str:
    return type_repr(field.type_spec, field.children)


def classify_widening(old: TypeSpec, new: TypeSpec) -> str | None:
    """Return widen/narrow for primitive pairs, otherwise None."""
    if old.kind == new.kind:
        return None
    if (old.kind, new.kind) in WIDENING_PAIRS:
        return "widen"
    if (new.kind, old.kind) in WIDENING_PAIRS:
        return "narrow"
    return None


def normalize_widening(expression: Any) -> str:
    """Normalize one allowed_type_widening declaration into 'a->b'."""
    if not isinstance(expression, str):
        raise SchemaGateError("SG-LOAD-010", "allowed_type_widening entries must be strings")
    parts = [part.strip() for part in expression.split("->")]
    if len(parts) != 2:
        raise SchemaGateError(
            "SG-LOAD-010",
            "allowed_type_widening entry must use the form int32->int64",
            {"entry": expression},
        )
    old, new = parse_type(parts[0]).kind, parse_type(parts[1]).kind
    if (old, new) not in WIDENING_PAIRS:
        raise SchemaGateError(
            "SG-LOAD-010",
            "unsupported type widening pair",
            {"entry": expression},
        )
    return f"{old}->{new}"


def is_known_kind(kind: str) -> bool:
    return kind in KNOWN_TYPE_KINDS


def decimal_direction(old: TypeSpec, new: TypeSpec) -> str:
    """Describe a decimal precision/scale move conservatively."""
    old_integer = (old.precision or 0) - (old.scale or 0)
    new_integer = (new.precision or 0) - (new.scale or 0)
    if new_integer >= old_integer and (new.scale or 0) >= (old.scale or 0):
        return "widen"
    if new_integer <= old_integer and (new.scale or 0) <= (old.scale or 0):
        return "narrow"
    return "mixed"
