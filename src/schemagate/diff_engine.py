"""Structured schema diffing with explicit rename evidence."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json
from .models import CaseData, Change, FieldSpec, SchemaVersion
from .naming import change_id, field_evidence, pair_id
from .partition_rules import diff_partition_specs
from .type_rules import (
    aggregate_directions,
    classify_widening,
    compare_constraints,
    decimal_direction,
    field_type_signature,
)

_CONSTRAINT_SEVERITY = {"widen": 0, "incomparable": 1, "narrow": 2}


class _Differ:
    def __init__(self, table: str, old: SchemaVersion, new: SchemaVersion):
        self.table, self.old, self.new = table, old, new
        self.pair = pair_id(old.version, new.version)
        self.changes: list[Change] = []
    def _change(self, change_type: str, path: str, evidence_version: str,
                aspect: str, summary: str,
                details: dict[str, Any] | None = None) -> None:
        self.changes.append(Change(
            change_id=change_id(self.table, self.pair, path, change_type),
            pair_id=self.pair,
            from_version=self.old.version,
            to_version=self.new.version,
            change_type=change_type,
            field_path=path,
            evidence_path=field_evidence(self.table, evidence_version, path, aspect),
            summary=summary,
            details=details or {},
        ))
    @staticmethod
    def _match(old_fields: tuple[FieldSpec, ...], new_fields: tuple[FieldSpec, ...]):
        new_by_name = {field.name: field for field in new_fields}
        old_by_name = {field.name: field for field in old_fields}
        matches = [(field, new_by_name[field.name], False)
                   for field in old_fields if field.name in new_by_name]
        unused_old = [field for field in old_fields if field.name not in new_by_name]
        unused_new = [field for field in new_fields if field.name not in old_by_name]
        claimed: set[str] = set()
        for field in unused_old:
            candidates = []
            for candidate in unused_new:
                same_id = (field.field_id is not None
                           and field.field_id == candidate.field_id)
                alias_link = candidate.name in field.aliases or field.name in candidate.aliases
                if candidate.name not in claimed and (same_id or alias_link):
                    candidates.append(candidate)
            if candidates:
                chosen = min(candidates, key=lambda item: item.name)
                matches.append((field, chosen, True))
                claimed.add(chosen.name)
        matched_old = {item[0].name for item in matches}
        matched_new = {item[1].name for item in matches}
        unmatched_old = [field for field in old_fields if field.name not in matched_old]
        unmatched_new = [field for field in new_fields if field.name not in matched_new]
        return matches, unmatched_old, unmatched_new
    def _order_change(self, matches, new_fields: tuple[FieldSpec, ...],
                      parent_path: str) -> None:
        shared_new = {new.name for _, new, _ in matches}
        old_sequence = [new.name for _, new, _ in
                        sorted(matches, key=lambda item: item[0].order)]
        new_sequence = [field.name for field in new_fields if field.name in shared_new]
        if old_sequence == new_sequence:
            return
        path = f"{parent_path}.$fields" if parent_path else "$fields"
        self._change("field_order_change", path, self.new.version, "order",
                     f"field order changed for {len(old_sequence)} shared field(s)",
                     {"old_order": old_sequence, "new_order": new_sequence})
    def diff_field_list(self, old_fields: tuple[FieldSpec, ...],
                        new_fields: tuple[FieldSpec, ...], parent_path: str) -> None:
        matches, unmatched_old, unmatched_new = self._match(old_fields, new_fields)
        self._order_change(matches, new_fields, parent_path)
        for field in sorted(unmatched_old, key=lambda item: (item.order, item.name)):
            self._change(
                "drop_field", field.path, self.old.version, "presence",
                f"field dropped: {field.path} ({field_type_signature(field)})",
                {"type": field_type_signature(field), "field_id": field.field_id,
                 "required": field.required},
            )
        for field in sorted(unmatched_new, key=lambda item: (item.order, item.name)):
            self._change(
                "add_field", field.path, self.new.version, "presence",
                f"field added: {field.path} ({field_type_signature(field)})",
                {"type": field_type_signature(field), "nullable": field.nullable,
                 "has_default": field.has_default, "default": field.default,
                 "required": field.required, "field_id": field.field_id},
            )
        for old, new, renamed in sorted(
                matches, key=lambda item: (item[1].order, item[1].name)):
            if renamed:
                self._change(
                    "rename_field", new.path, self.new.version, "name",
                    f"field explicitly renamed: {old.path} -> {new.path}",
                    {"old_path": old.path, "new_path": new.path,
                     "field_id": new.field_id, "old_aliases": list(old.aliases),
                     "new_aliases": list(new.aliases)},
                )
            self.compare_field(old, new)
    def compare_field(self, old: FieldSpec, new: FieldSpec) -> None:
        if old.nullable != new.nullable:
            self._change(
                "nullable_to_true" if new.nullable else "nullable_to_false",
                new.path, self.new.version, "nullable",
                f"nullable changed: {old.path} {old.nullable} -> {new.nullable}",
                {"old_nullable": old.nullable, "new_nullable": new.nullable},
            )
        if old.has_default != new.has_default:
            self._change(
                "default_added" if new.has_default else "default_removed",
                new.path, self.new.version, "default",
                f"default {'added' if new.has_default else 'removed'} for {new.path}",
                {"old_default": old.default, "new_default": new.default},
            )
        elif old.has_default and canonical_json(old.default) != canonical_json(new.default):
            self._change("default_changed", new.path, self.new.version, "default",
                         f"default changed for {new.path}",
                         {"old_default": old.default, "new_default": new.default})
        # Struct-level constraints are merged with descendant constraint changes
        # in compare_types so both share one aggregate change ID. A struct that
        # becomes a scalar is handled here because no child diff exists.
        if not (old.type_spec.kind == new.type_spec.kind == "struct"):
            entries = compare_constraints(old.constraints, new.constraints)
            if entries:
                self._emit_constraint_change(old, new, entries)
        self.compare_types(old, new)
    def _emit_constraint_change(self, old: FieldSpec, new: FieldSpec,
                                entries) -> None:
        direction = aggregate_directions(entries)
        self._change(
            "constraint_change", new.path, self.new.version, "constraints",
            f"constraints {direction} for {new.path}: "
            f"{self._constraint_fragments(entries)}",
            {
                "old_constraints": old.constraints,
                "new_constraints": new.constraints,
                "direction": direction,
                "parent_path": new.path if new.type_spec.kind == "struct" else (
                    new.path.rsplit(".", 1)[0] if "." in new.path else ""
                ),
                "changed_paths": [new.path],
                "constraint_changes": list(entries),
            },
        )
    @staticmethod
    def _constraint_value(value: Any) -> str:
        if value is None:
            return "unbounded"
        return canonical_json(value)
    @classmethod
    def _constraint_fragments(cls, entries) -> str:
        fragments = []
        for entry in entries:
            name, direction = entry["constraint"], entry["direction"]
            if entry["change"] == "added":
                fragments.append(
                    f"{name} added ({direction}; {cls._constraint_value(entry['new_value'])})"
                )
            elif entry["change"] == "removed":
                fragments.append(
                    f"{name} removed ({direction}; was {cls._constraint_value(entry['old_value'])})"
                )
            else:
                fragments.append(
                    f"{name} {direction} "
                    f"({cls._constraint_value(entry['old_value'])} -> "
                    f"{cls._constraint_value(entry['new_value'])})"
                )
        return "; ".join(fragments)
    def _emit_struct_constraint_change(
        self,
        old: FieldSpec,
        new: FieldSpec,
        own_entries,
        child_constraint_changes: list[Change],
    ) -> None:
        """Aggregate descendant constraint changes at the struct path.

        Details keep the parent path and the affected child paths so the
        aggregate remains explainable without silently replacing the child
        findings, which are still emitted and evaluated on their own paths.
        """
        children: dict[str, dict[str, Any]] = {}
        for change in child_constraint_changes:
            details = change.details
            if details.get("aggregated"):
                nested = details.get("children", [])
            else:
                nested = [{
                    "path": change.field_path,
                    "direction": details.get("direction", "incomparable"),
                    "constraint_changes": details.get("constraint_changes", []),
                }]
            for child in nested:
                path = child.get("path")
                direction = child.get("direction", "incomparable")
                if not isinstance(path, str):
                    continue
                previous = children.get(path)
                if (previous is None or
                        _CONSTRAINT_SEVERITY.get(direction, 1) >
                        _CONSTRAINT_SEVERITY.get(previous["direction"], 1)):
                    children[path] = {
                        "path": path,
                        "direction": direction,
                        "constraint_changes": child.get("constraint_changes", []),
                    }
        if own_entries:
            own = {
                "path": new.path,
                "direction": aggregate_directions(own_entries),
                "constraint_changes": list(own_entries),
            }
            previous = children.get(new.path)
            if (previous is None or
                    _CONSTRAINT_SEVERITY[own["direction"]] >
                    _CONSTRAINT_SEVERITY[previous["direction"]]):
                children[new.path] = own
        ordered = [children[path] for path in sorted(children)]
        direction = max(
            (child["direction"] for child in ordered),
            key=lambda value: _CONSTRAINT_SEVERITY.get(value, 1),
            default="incomparable",
        )
        fragments = "; ".join(
            f"{child['path']} {child['direction']}"
            + (f" ({self._constraint_fragments(child['constraint_changes'])})"
               if child.get("constraint_changes") else "")
            for child in ordered
        )
        self._change(
            "constraint_change", new.path, self.new.version, "constraints",
            f"constraint changes under {new.path} ({direction}): {fragments}",
            {
                "aggregated": True,
                "old_constraints": old.constraints,
                "new_constraints": new.constraints,
                "direction": direction,
                "parent_path": new.path,
                "changed_paths": [child["path"] for child in ordered],
                "children": ordered,
            },
        )
    def compare_types(self, old: FieldSpec, new: FieldSpec) -> None:
        old_type, new_type = old.type_spec, new.type_spec
        if old_type.kind == new_type.kind == "struct":
            own_entries = compare_constraints(old.constraints, new.constraints)
            before = len(self.changes)
            self.diff_field_list(old.children, new.children, new.path)
            child_changes = self.changes[before:]
            child_constraint_changes = [
                item for item in child_changes if item.change_type == "constraint_change"
            ]
            if own_entries or child_constraint_changes:
                self._emit_struct_constraint_change(
                    old, new, own_entries, child_constraint_changes
                )
            # Constraint changes are absorbed by the per-path constraint
            # findings above. Do not repeat them inside the generic struct
            # aggregate: a flat REVIEW there could mask a more severe child
            # determination (for example a narrowed range).
            remaining = [
                item for item in child_changes
                if item.change_type != "constraint_change"
            ]
            if remaining:
                paths = sorted({item.field_path for item in remaining})
                self._change(
                    "struct_field_change", new.path, self.new.version, "fields",
                    f"nested struct fields changed under {new.path}: {len(paths)} path(s)",
                    {"changed_paths": paths, "type": field_type_signature(new)},
                )
            return
        old_sig, new_sig = field_type_signature(old), field_type_signature(new)
        if old_sig == new_sig:
            return
        if old_type.kind == new_type.kind and old_type.kind in {"list", "map"}:
            self._change(
                "element_type_change", new.path, self.new.version, "type",
                f"nested collection type changed for {new.path}: {old_sig} -> {new_sig}",
                {"old_type": old_sig, "new_type": new_sig, "collection": old_type.kind},
            )
            return
        if old_type.kind == new_type.kind == "decimal":
            direction = decimal_direction(old_type, new_type)
            self._change(
                "decimal_change", new.path, self.new.version, "type",
                f"decimal changed for {new.path}: {old_sig} -> {new_sig} ({direction})",
                {"old_type": old_sig, "new_type": new_sig, "direction": direction,
                 "old_precision": old_type.precision, "old_scale": old_type.scale,
                 "new_precision": new_type.precision, "new_scale": new_type.scale},
            )
            return
        if old_type.kind.startswith("timestamp_") and new_type.kind.startswith("timestamp_"):
            self._change(
                "timestamp_unit_change", new.path, self.new.version, "type",
                f"timestamp unit changed for {new.path}: {old_type.kind} -> {new_type.kind}",
                {"old_type": old_sig, "new_type": new_sig},
            )
            return
        direction = classify_widening(old_type, new_type)
        if direction is not None:
            self._change(
                "widen_type" if direction == "widen" else "narrow_type",
                new.path, self.new.version, "type",
                f"type {direction}ed for {new.path}: {old_sig} -> {new_sig}",
                {"old_type": old_sig, "new_type": new_sig, "direction": direction},
            )
            return
        self._change(
            "type_change", new.path, self.new.version, "type",
            f"incompatible type change for {new.path}: {old_sig} -> {new_sig}",
            {"old_type": old_sig, "new_type": new_sig, "old_kind": old_type.kind,
             "new_kind": new_type.kind},
        )


def diff_versions(case: CaseData, old: SchemaVersion,
                  new: SchemaVersion) -> tuple[Change, ...]:
    differ = _Differ(case.table, old, new)
    differ.diff_field_list(old.fields, new.fields, "")
    differ.changes.extend(diff_partition_specs(
        case.table, old.version, new.version,
        case.partitions.get(old.version), case.partitions.get(new.version),
    ))
    return tuple(sorted(differ.changes, key=lambda item: (
        item.field_path, item.change_type, item.change_id
    )))
