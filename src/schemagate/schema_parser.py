"""Parsing and validation for declarative schema documents."""

from __future__ import annotations

import re
from typing import Any

from .canonical import normalize_json_value
from .errors import SchemaGateError
from .models import FieldSpec, SchemaVersion, TypeSpec
from .type_rules import invalid_type, parse_type

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")

_BASE_KEYS = {
    "name", "type", "nullable", "default", "field_id", "aliases", "doc",
    "constraints", "fields", "element_type", "key_type", "value_type",
}


def _document_error(message: str, **details: Any) -> SchemaGateError:
    return SchemaGateError("SG-LOAD-008", message, details)


def _require_text(raw: dict[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _document_error(f"{label}.{key} must be a non-empty string")
    return value.strip()


def _parse_aliases(raw: Any, path: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _document_error("aliases must be a list of strings", path=path)
    aliases = set()
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise _document_error("aliases must contain non-empty strings", path=path)
        candidate = item.strip()
        if not _NAME_RE.match(candidate):
            raise _document_error("alias is not a valid field name", path=path, alias=candidate)
        aliases.add(candidate)
    return tuple(sorted(aliases))


def _parse_type_block(raw: dict[str, Any], path: str) -> tuple[TypeSpec, tuple[FieldSpec, ...]]:
    if "type" not in raw:
        raise invalid_type(None, f"field {path} has no type")
    declared = raw["type"]
    if not isinstance(declared, str):
        raise invalid_type(declared, f"field {path} type must be a string")
    lowered = " ".join(declared.strip().lower().split())
    has_children = "fields" in raw
    has_element = "element_type" in raw
    has_key_value = "key_type" in raw or "value_type" in raw
    if lowered == "struct":
        if has_element or has_key_value:
            raise invalid_type(declared, "struct fields use fields, not element/key types")
        if not isinstance(raw.get("fields"), list) or not raw["fields"]:
            raise _document_error("struct fields must be a non-empty list", path=path)
        children = parse_field_list(raw["fields"], parent_path=path)
        return TypeSpec("struct"), children
    if lowered in {"list", "map"}:
        if has_children:
            raise _document_error("nested fields are only valid for struct type", path=path)
        if lowered == "list":
            if not has_element or has_key_value:
                raise _document_error("list requires element_type", path=path)
            element_type = parse_type(raw["element_type"])
            return TypeSpec("list", element=element_type), ()
        if not has_key_value:
            raise _document_error("map requires key_type and value_type", path=path)
        key_type = parse_type(raw.get("key_type"))
        value_type = parse_type(raw.get("value_type"))
        if key_type.kind not in {
            "boolean", "int32", "int64", "float32", "float64", "string",
            "binary", "date", "timestamp_ms", "timestamp_us", "decimal", "uuid",
        }:
            raise invalid_type(declared, "map key type must be primitive")
        return TypeSpec("map", key=key_type, value=value_type), ()
    if has_children or has_element or has_key_value:
        raise _document_error("nested type attributes require struct/list/map", path=path)
    return parse_type(declared), ()


def _parse_field(
    raw: Any,
    parent_path: str,
    order: int,
    seen_paths: dict[str, str],
    seen_ids: dict[int, str],
) -> FieldSpec:
    if not isinstance(raw, dict):
        raise _document_error("field entries must be mappings", path=parent_path or "$")
    unknown = sorted(set(raw) - _BASE_KEYS)
    if unknown:
        raise _document_error("unknown field attributes", attributes=unknown, path=parent_path or "$")
    name = _require_text(raw, "name", "field")
    if not _NAME_RE.match(name):
        raise _document_error("field name is not a simple identifier", name=name)
    path = f"{parent_path}.{name}" if parent_path else name
    if path in seen_paths:
        raise SchemaGateError(
            "SG-LOAD-002",
            "duplicate field name",
            {"path": path, "first_occurrence": seen_paths[path]},
        )
    type_spec, children = _parse_type_block(raw, path)
    nullable = raw.get("nullable", True)
    if not isinstance(nullable, bool):
        raise _document_error("nullable must be a boolean", path=path)
    has_default = "default" in raw
    default = normalize_json_value(raw.get("default"), path=f"{path}.default")
    field_id = raw.get("field_id")
    if field_id is not None:
        if isinstance(field_id, bool) or not isinstance(field_id, int) or field_id <= 0:
            raise _document_error("field_id must be a positive integer", path=path)
        if field_id in seen_ids:
            raise _document_error(
                "duplicate field_id",
                path=path,
                field_id=field_id,
                first_occurrence=seen_ids[field_id],
            )
        seen_ids[field_id] = path
    doc = raw.get("doc", "")
    if not isinstance(doc, str):
        raise _document_error("doc must be a string", path=path)
    constraints = raw.get("constraints", {})
    if not isinstance(constraints, dict):
        raise _document_error("constraints must be a mapping", path=path)
    normalized_constraints = normalize_json_value(constraints, path=f"{path}.constraints")
    seen_paths[path] = path
    aliases = _parse_aliases(raw.get("aliases"), path)
    return FieldSpec(
        name=name,
        path=path,
        type_spec=type_spec,
        nullable=nullable,
        has_default=has_default,
        default=default,
        field_id=field_id,
        aliases=aliases,
        doc=doc,
        constraints=normalized_constraints,
        children=children,
        order=order,
    )


def parse_field_list(raw_fields: Any, parent_path: str = "") -> tuple[FieldSpec, ...]:
    if not isinstance(raw_fields, list):
        raise _document_error("fields must be a list", path=parent_path or "$")
    seen_paths: dict[str, str] = {}
    seen_ids: dict[int, str] = {}
    fields = []
    for index, raw in enumerate(raw_fields):
        fields.append(_parse_field(raw, parent_path, index, seen_paths, seen_ids))
    return tuple(fields)


def parse_schema_document(
    document: Any,
    *,
    expected_version: str | None = None,
    expected_table: str | None = None,
) -> SchemaVersion:
    """Convert a YAML-decoded schema document into a SchemaVersion."""
    if not isinstance(document, dict):
        raise _document_error("schema document must be a mapping")
    table = _require_text(document, "table", "schema")
    version = _require_text(document, "version", "schema")
    if not _VERSION_RE.match(version):
        raise _document_error("version label contains unsupported characters", version=version)
    if expected_version is not None and version != expected_version:
        raise _document_error(
            "schema version does not match manifest",
            expected=expected_version,
            actual=version,
        )
    if expected_table is not None and table != expected_table:
        raise _document_error(
            "schema table does not match manifest",
            expected=expected_table,
            actual=table,
        )
    fields = parse_field_list(document.get("fields"), "")
    if not fields:
        raise _document_error("schema must declare at least one field", version=version)
    return SchemaVersion(version=version, table=table, fields=fields)
