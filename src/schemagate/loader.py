"""Local case discovery, safe file loading, and case validation."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from .canonical import input_fingerprint, schema_fingerprint
from .contracts_parser import parse_consumer, parse_partition_spec, parse_policy
from .errors import SchemaGateError
from .models import CaseData, SchemaVersion
from .schema_parser import parse_schema_document

_CASE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_mapping(loader: yaml.Loader, node: yaml.Node, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"duplicate key: {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)
# Load ISO-looking scalars as strings; schema/default values remain JSON-safe.
_UniqueKeyLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in entries
            if tag != "tag:yaml.org,2002:timestamp"]
    for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _load_error(message: str, **details: Any) -> SchemaGateError:
    return SchemaGateError("SG-LOAD-001", message, details)


def read_yaml(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _load_error("required input file is missing", file=str(path)) from exc
    except OSError as exc:
        raise _load_error("input file could not be read", file=str(path)) from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise _load_error("input file is not valid YAML", file=str(path)) from exc


def _text(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SchemaGateError("SG-LOAD-008", f"{label}.{key} must be a non-empty string")
    return value.strip()


def _safe_file(case_dir: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise SchemaGateError("SG-LOAD-013", "case file path must be a non-empty relative path")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise SchemaGateError("SG-LOAD-013", "absolute paths are not allowed", path=relative)
    resolved = (case_dir / candidate).resolve()
    base = case_dir.resolve()
    if resolved != base and base not in resolved.parents:
        raise SchemaGateError("SG-LOAD-013", "path escapes the case directory", path=relative)
    return resolved


def _manifest_file_entries(
    raw: Any, label: str, allow_empty: bool = False
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise SchemaGateError("SG-LOAD-008", f"{label} must be a non-empty list")
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise SchemaGateError("SG-LOAD-008", f"{label} entries must be mappings")
        allowed = {"file", "version", "description"} if label != "consumers" else {"file", "description"}
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise SchemaGateError("SG-LOAD-008", "unknown manifest entry attributes", attributes=unknown)
        entries.append(item)
    return entries


class CaseLoader:
    """Loads only registered case directories beneath a fixed fixtures root."""
    def __init__(self, fixtures_dir: str | Path):
        self.fixtures_dir = Path(fixtures_dir).resolve()
        if not self.fixtures_dir.is_dir():
            raise _load_error("fixtures directory is missing", dir=str(self.fixtures_dir))
        self._registry: dict[str, Path] = {}
        self._summaries: dict[str, dict[str, Any]] = {}
        self._discover()
    def _discover(self) -> None:
        for child in sorted(self.fixtures_dir.iterdir(), key=lambda item: item.name):
            manifest = child / "manifest.yaml"
            if not child.is_dir() or not manifest.is_file():
                continue
            document = read_yaml(manifest)
            if not isinstance(document, dict):
                raise SchemaGateError("SG-LOAD-008", "manifest must be a mapping", case_dir=child.name)
            case_id = _text(document, "case_id", "manifest")
            if not _CASE_RE.match(case_id):
                raise SchemaGateError("SG-LOAD-008", "case_id is not a valid identifier", case_id=case_id)
            if case_id != child.name:
                raise SchemaGateError(
                    "SG-LOAD-008",
                    "manifest case_id must match its directory name",
                    case_id=case_id,
                    directory=child.name,
                )
            if case_id in self._registry:
                raise SchemaGateError("SG-LOAD-012", "duplicate case_id", case_id=case_id)
            versions = _manifest_file_entries(document.get("schema_versions"), "schema_versions")
            consumers = _manifest_file_entries(document.get("consumers"), "consumers", True)
            self._registry[case_id] = child.resolve()
            self._summaries[case_id] = {
                "case_id": case_id,
                "table": _text(document, "table", "manifest"),
                "description": str(document.get("description", "")),
                "versions": [str(item.get("version", "")) for item in versions],
                "schema_count": len(versions),
                "consumer_count": len(consumers),
            }
    def case_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._registry))
    def list_cases(self) -> list[dict[str, Any]]:
        return [dict(self._summaries[case_id]) for case_id in sorted(self._summaries)]
    def load_case(self, case_id: str) -> CaseData:
        if not isinstance(case_id, str) or case_id not in self._registry:
            raise SchemaGateError("SG-LOAD-007", "unknown case_id", case_id=case_id)
        case_dir = self._registry[case_id]
        manifest = read_yaml(case_dir / "manifest.yaml")
        assert isinstance(manifest, dict)
        table = _text(manifest, "table", "manifest")
        description = manifest.get("description", "")
        if not isinstance(description, str):
            raise SchemaGateError("SG-LOAD-008", "manifest.description must be a string")
        policy_doc = read_yaml(_safe_file(case_dir, manifest.get("policy")))
        policy = parse_policy(policy_doc)
        schema_entries = _manifest_file_entries(manifest.get("schema_versions"), "schema_versions")
        versions: list[SchemaVersion] = []
        seen_versions: set[str] = set()
        for entry in schema_entries:
            version_label = _text(entry, "version", "schema_versions")
            if not _VERSION_RE.match(version_label):
                raise SchemaGateError("SG-LOAD-008", "invalid version label", version=version_label)
            if version_label in seen_versions:
                raise SchemaGateError("SG-LOAD-004", "duplicate schema version", version=version_label)
            seen_versions.add(version_label)
            document = read_yaml(_safe_file(case_dir, entry.get("file")))
            parsed = parse_schema_document(
                document, expected_version=version_label, expected_table=table
            )
            versions.append(replace(parsed, schema_fingerprint=schema_fingerprint(parsed)))
        if not versions:
            raise SchemaGateError("SG-LOAD-008", "case must declare at least one schema version")
        partitions: dict[str, Any] = {version.version: None for version in versions}
        partition_entries = manifest.get("partition_specs", [])
        if partition_entries is None:
            partition_entries = []
        if not isinstance(partition_entries, list):
            raise SchemaGateError("SG-LOAD-009", "partition_specs must be a list")
        seen_partitions: set[str] = set()
        for entry in partition_entries:
            if not isinstance(entry, dict):
                raise SchemaGateError("SG-LOAD-009", "partition_specs entries must be mappings")
            version_label = _text(entry, "version", "partition_specs")
            if version_label in seen_partitions:
                raise SchemaGateError("SG-LOAD-009", "duplicate partition spec", version=version_label)
            if version_label not in seen_versions:
                raise SchemaGateError("SG-LOAD-009", "partition spec references unknown version")
            seen_partitions.add(version_label)
            document = read_yaml(_safe_file(case_dir, entry.get("file")))
            partitions[version_label] = parse_partition_spec(
                document, expected_version=version_label, expected_table=table
            )
        consumer_entries = _manifest_file_entries(manifest.get("consumers"), "consumers", True)
        consumers = []
        seen_consumers: set[str] = set()
        for entry in consumer_entries:
            document = read_yaml(_safe_file(case_dir, entry.get("file")))
            consumer = parse_consumer(
                document,
                expected_table=table,
                known_versions=tuple(version.version for version in versions),
                default_mode=policy.default_mode,
            )
            if consumer.consumer_id in seen_consumers:
                raise SchemaGateError(
                    "SG-LOAD-010", "duplicate consumer_id", consumer_id=consumer.consumer_id
                )
            seen_consumers.add(consumer.consumer_id)
            consumers.append(consumer)
        if not consumers:
            raise SchemaGateError("SG-LOAD-006", "case must declare at least one consumer")
        case = CaseData(
            case_id=case_id,
            table=table,
            description=description,
            versions=tuple(versions),
            partitions=partitions,
            consumers=tuple(sorted(consumers, key=lambda item: item.consumer_id)),
            policy=policy,
        )
        return replace(case, input_fingerprint=input_fingerprint(case))
