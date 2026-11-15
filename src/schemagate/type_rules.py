"""Schema type grammar, normalization, and widening classification."""

from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Declarative constraint classification
# ---------------------------------------------------------------------------
#
# Constraints live in FieldSpec.constraints as a plain mapping. Two
# representations are accepted for enum-like constraints:
#
#   constraints: {enum: [a, b]}
#   constraints: {values: [a, b]}          (alias for enum)
#   constraints: {enum: {values: [a, b]}}  (explicit form)
#
# and for the enumerated-kind spelling used by the lake_events fixture:
#
#   type: enum
#   constraints: {values: [a, b]}
#
# For every supported constraint family this module decides whether the
# allowed value set grew ("widen"), shrank ("narrow"), or cannot be compared
# ("incomparable"). Incomparable is deliberately never forced into a numeric
# ordering: a pattern rewrite, an unparsable pair, or a change between families
# carries no comparable order.

RANGE_CONSTRAINTS = ("min", "max", "exclusive_min", "exclusive_max")
LENGTH_CONSTRAINTS = (
    "max_length", "min_length", "exact_length",
    "max_items", "min_items", "exact_items",
)
ENUM_CONSTRAINTS = ("enum", "values", "allowed_values", "enum_values")
PATTERN_CONSTRAINTS = ("pattern", "regex")
CHARSET_CONSTRAINTS = ("charset", "character_set")
KNOWN_CONSTRAINTS = frozenset(
    RANGE_CONSTRAINTS + LENGTH_CONSTRAINTS + ENUM_CONSTRAINTS
    + PATTERN_CONSTRAINTS + CHARSET_CONSTRAINTS
)
CONSTRAINT_KINDS = frozenset({"range", "length", "enum", "pattern", "charset"})
CONSTRAINT_DIRECTIONS = frozenset({"widen", "narrow", "incomparable"})


def constraint_kind(name: Any) -> str | None:
    """Map a constraint attribute name to its comparison family."""
    if name in RANGE_CONSTRAINTS:
        return "range"
    if name in LENGTH_CONSTRAINTS:
        return "length"
    if name in ENUM_CONSTRAINTS:
        return "enum"
    if name in PATTERN_CONSTRAINTS:
        return "pattern"
    if name in CHARSET_CONSTRAINTS:
        return "charset"
    return None


_CONSTRAINT_ALIASES = {
    **{name: "enum" for name in ENUM_CONSTRAINTS},
    **{name: "pattern" for name in PATTERN_CONSTRAINTS},
    **{name: "charset" for name in CHARSET_CONSTRAINTS},
}


def canonical_constraint_name(name: str) -> str:
    """Collapse alias spellings so a rename of the key is not a change."""
    return _CONSTRAINT_ALIASES.get(name, name)


def _numeric(value: Any) -> int | float | None:
    """Return a comparable number, rejecting booleans and strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _enum_values(value: Any) -> tuple[Any, ...] | None:
    """Normalize the accepted enum spellings into a hashable value set."""
    if isinstance(value, dict):
        nested = value.get("values")
        if nested is None and len(value) == 1:
            nested = next(iter(value.values()))
        return _enum_values(nested)
    if isinstance(value, list):
        normalized = []
        for item in value:
            if isinstance(item, (dict, list)):
                normalized.append(json.dumps(item, sort_keys=True, separators=(",", ":")))
            else:
                normalized.append(item)
        return tuple(sorted(normalized, key=lambda item: (str(type(item)), str(item))))
    return None


def _compare_range(name: str, old_value: Any, new_value: Any) -> str:
    old_number, new_number = _numeric(old_value), _numeric(new_value)
    if old_number is None or new_number is None:
        return "incomparable"
    if old_number == new_number:
        return "widen"
    # min / exclusive_min: a smaller bound admits more values (widen).
    # max / exclusive_max: a larger bound admits more values (widen).
    if name in {"min", "exclusive_min"}:
        return "widen" if new_number < old_number else "narrow"
    return "widen" if new_number > old_number else "narrow"


def _compare_length(name: str, old_value: Any, new_value: Any) -> str:
    old_number, new_number = _numeric(old_value), _numeric(new_value)
    if old_number is None or new_number is None:
        return "incomparable"
    if old_number == new_number:
        return "widen"
    if name.startswith("exact"):
        # exact_length / exact_items admit a single value; any different
        # value is a disjoint set, not an ordered widening or narrowing.
        return "incomparable"
    if name.startswith("max"):
        # A larger allowed length admits more values (widen).
        return "widen" if new_number > old_number else "narrow"
    # min_length: a smaller floor admits more values (widen).
    return "widen" if new_number < old_number else "narrow"


def _compare_enum(old_value: Any, new_value: Any) -> str:
    old_set, new_set = _enum_values(old_value), _enum_values(new_value)
    if old_set is None or new_set is None:
        return "incomparable"
    if set(old_set) == set(new_set):
        return "widen"
    if set(new_set) > set(old_set):
        return "widen"
    if set(new_set) < set(old_set):
        return "narrow"
    return "incomparable"


def _compare_pattern(old_value: Any, new_value: Any) -> str:
    if old_value == new_value:
        return "widen"
    # A rewritten regular expression has no comparable order without a regex
    # inclusion solver; classify conservatively.
    return "incomparable"


def _compare_charset(old_value: Any, new_value: Any) -> str:
    if old_value == new_value:
        return "widen"
    return "incomparable"


def compare_constraint(name: str, old_value: Any, new_value: Any) -> str:
    """Classify one constraint attribute as widen/narrow/incomparable."""
    kind = constraint_kind(name)
    if kind is None:
        return "incomparable"
    if kind == "range":
        return _compare_range(name, old_value, new_value)
    if kind == "length":
        return _compare_length(name, old_value, new_value)
    if kind == "enum":
        return _compare_enum(old_value, new_value)
    if kind == "pattern":
        return _compare_pattern(old_value, new_value)
    return _compare_charset(old_value, new_value)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _constraint_map(constraints: dict[str, Any]) -> dict[str, Any] | None:
    """Index constraints by canonical name, flagging alias collisions.

    Returns None when one side declares two aliases for the same family (for
    example both ``enum`` and ``values``); such a pair cannot be compared
    without guessing which declaration wins.
    """
    indexed: dict[str, Any] = {}
    for name, value in constraints.items():
        canonical = canonical_constraint_name(name)
        if canonical in indexed:
            return None
        indexed[canonical] = value
    return indexed


def compare_constraints(
    old_constraints: dict[str, Any], new_constraints: dict[str, Any]
) -> tuple[dict[str, Any], ...]:
    """Compare two constraint mappings attribute by attribute.

    Returns one entry per differing attribute with a canonical direction and
    the concrete before/after values. Attributes present on only one side are
    treated as unbounded on the missing side when the family is ordered (so a
    newly added max is a tightening), and as incomparable otherwise.
    """
    old_map, new_map = _constraint_map(old_constraints), _constraint_map(new_constraints)
    if old_map is None or new_map is None:
        if _canonical(old_constraints) == _canonical(new_constraints):
            return ()
        return ({
            "constraint": "constraints",
            "kind": "unknown",
            "change": "changed",
            "direction": "incomparable",
            "old_value": old_constraints,
            "new_value": new_constraints,
        },)
    entries: list[dict[str, Any]] = []
    for name in sorted(set(old_map) | set(new_map)):
        kind = constraint_kind(name) or "unknown"
        if name in old_map and name in new_map:
            old_value, new_value = old_map[name], new_map[name]
            if _canonical(old_value) == _canonical(new_value):
                continue
            change = "changed"
            direction = compare_constraint(name, old_value, new_value)
        elif name in new_map:
            old_value, new_value = None, new_map[name]
            change = "added"
            direction = _added_direction(kind, name, new_value)
        else:
            old_value, new_value = old_map[name], None
            change = "removed"
            direction = _removed_direction(kind, name, old_value)
        entries.append({
            "constraint": name,
            "kind": kind,
            "change": change,
            "direction": direction,
            "old_value": old_value,
            "new_value": new_value,
        })
    return tuple(entries)


def _added_direction(kind: str, name: str, value: Any) -> str:
    """A constraint introduced on the new side."""
    if kind == "range":
        return "narrow"      # new bound where none existed tightens admission
    if kind == "length":
        return "narrow"
    if kind in {"enum", "pattern", "charset"}:
        return "narrow" if _has_value(value) else "incomparable"
    return "incomparable"


def _removed_direction(kind: str, name: str, value: Any) -> str:
    """A constraint dropped from the new side."""
    if kind == "range" or kind == "length":
        return "widen"       # removing a bound admits more values
    if kind in {"enum", "pattern", "charset"}:
        return "widen" if _has_value(value) else "incomparable"
    return "incomparable"


def _has_value(value: Any) -> bool:
    return value is not None and value != [] and value != ""


def aggregate_directions(entries: tuple[dict[str, Any], ...]) -> str:
    """Collapse per-attribute directions into one field-level direction.

    A definite tightening dominates an incomparable attribute: if any changed
    attribute narrows the accepted set, the field is narrowed. Incomparable
    only wins when no comparable tightening exists.
    """
    directions = {entry["direction"] for entry in entries}
    if not directions:
        return "widen"
    if "narrow" in directions:
        return "narrow"
    if "incomparable" in directions:
        return "incomparable"
    return "widen"
