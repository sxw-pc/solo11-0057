"""Compatibility policy evaluation for each consumer and change."""

from __future__ import annotations

from .config import SEVERITY_RANK
from .models import (
    CaseData,
    Change,
    ConsumerContract,
    ConsumerVerdict,
    FieldSpec,
    Finding,
    Policy,
    RuleOutcome,
    SchemaVersion,
)


def _outcome(severity: str, rule_id: str, message: str, **details: object) -> RuleOutcome:
    return RuleOutcome(severity, rule_id, message, dict(details))


def _allowed_widening(consumer: ConsumerContract, change: Change) -> bool:
    old_type = change.details.get("old_type")
    new_type = change.details.get("new_type")
    return isinstance(old_type, str) and isinstance(new_type, str) and (
        f"{old_type}->{new_type}" in consumer.allowed_type_widening
    )


def _backward(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    kind, path = change.change_type, change.field_path
    if kind == "add_field":
        if change.details.get("required"):
            return _outcome(
                "BLOCK", "SG-COMP-101",
                f"backward mode blocks a new required field at {path}",
            )
        return _outcome("PASS", "SG-COMP-102", f"optional field added at {path}")
    if kind == "drop_field":
        return _outcome("BLOCK", "SG-COMP-103", f"backward mode blocks field removal at {path}")
    if kind == "rename_field":
        return _outcome("REVIEW", "SG-COMP-104", f"explicit field rename requires review at {path}")
    if kind == "widen_type":
        if _allowed_widening(consumer, change):
            return _outcome("PASS", "SG-COMP-105", f"allowed type widening at {path}")
        return _outcome("REVIEW", "SG-COMP-106", f"type widening not declared for {path}")
    if kind == "narrow_type":
        return _outcome("BLOCK", "SG-COMP-107", f"backward mode blocks type narrowing at {path}")
    if kind == "type_change":
        return _outcome("BLOCK", "SG-COMP-108", f"backward mode blocks incompatible type change at {path}")
    if kind == "nullable_to_true":
        return _outcome("PASS", "SG-COMP-109", f"nullable relaxation accepted at {path}")
    if kind == "nullable_to_false":
        return _outcome("BLOCK", "SG-COMP-110", f"backward mode blocks nullable tightening at {path}")
    if kind == "default_added":
        return _outcome("PASS", "SG-COMP-111", f"default added at {path}")
    if kind == "default_removed":
        return _outcome("REVIEW", "SG-COMP-112", f"default removal requires review at {path}")
    if kind == "default_changed":
        return _outcome("REVIEW", "SG-COMP-113", f"default change requires review at {path}")
    if kind == "field_order_change":
        return _outcome("REVIEW", "SG-COMP-114", f"field order changed at {path}")
    if kind == "decimal_change":
        return _outcome("REVIEW", "SG-COMP-115", f"decimal precision or scale changed at {path}")
    if kind == "timestamp_unit_change":
        return _outcome("BLOCK", "SG-COMP-116", f"timestamp unit changed at {path}")
    if kind == "element_type_change":
        return _outcome("BLOCK", "SG-COMP-117", f"collection element type changed at {path}")
    if kind == "struct_field_change":
        return _outcome("REVIEW", "SG-COMP-118", f"nested struct fields changed at {path}")
    if kind == "constraint_change":
        return _outcome("REVIEW", "SG-COMP-119", f"field constraints changed at {path}")
    return _outcome("REVIEW", "SG-COMP-999", f"unclassified change at {path}")


def _forward(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    kind, path = change.change_type, change.field_path
    if kind == "add_field":
        if change.details.get("required"):
            return _outcome(
                "BLOCK", "SG-COMP-201",
                f"forward mode blocks a new required field at {path}",
            )
        return _outcome("PASS", "SG-COMP-202", f"optional field added at {path}")
    if kind == "drop_field":
        return _outcome("BLOCK", "SG-COMP-203", f"forward mode blocks field removal at {path}")
    if kind == "rename_field":
        return _outcome("REVIEW", "SG-COMP-204", f"explicit field rename requires review at {path}")
    if kind == "widen_type":
        if _allowed_widening(consumer, change):
            return _outcome("PASS", "SG-COMP-205", f"allowed type widening at {path}")
        return _outcome("REVIEW", "SG-COMP-206", f"type widening not declared for {path}")
    if kind == "narrow_type":
        return _outcome("BLOCK", "SG-COMP-207", f"forward mode blocks type narrowing at {path}")
    if kind == "type_change":
        return _outcome("BLOCK", "SG-COMP-208", f"forward mode blocks incompatible type change at {path}")
    if kind == "nullable_to_true":
        return _outcome("PASS", "SG-COMP-209", f"nullable relaxation accepted at {path}")
    if kind == "nullable_to_false":
        return _outcome("BLOCK", "SG-COMP-210", f"forward mode blocks nullable tightening at {path}")
    if kind == "default_added":
        return _outcome("REVIEW", "SG-COMP-211", f"default added at {path}")
    if kind == "default_removed":
        return _outcome("REVIEW", "SG-COMP-212", f"default removal requires review at {path}")
    if kind == "default_changed":
        return _outcome("REVIEW", "SG-COMP-213", f"default change requires review at {path}")
    if kind == "field_order_change":
        return _outcome("REVIEW", "SG-COMP-214", f"field order changed at {path}")
    if kind == "decimal_change":
        return _outcome("REVIEW", "SG-COMP-215", f"decimal precision or scale changed at {path}")
    if kind == "timestamp_unit_change":
        return _outcome("BLOCK", "SG-COMP-216", f"timestamp unit changed at {path}")
    if kind == "element_type_change":
        return _outcome("BLOCK", "SG-COMP-217", f"collection element type changed at {path}")
    if kind == "struct_field_change":
        return _outcome("REVIEW", "SG-COMP-218", f"nested struct fields changed at {path}")
    if kind == "constraint_change":
        return _outcome("REVIEW", "SG-COMP-219", f"field constraints changed at {path}")
    return _outcome("REVIEW", "SG-COMP-999", f"unclassified change at {path}")


def _full(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    backward = _backward(change, consumer)
    forward = _forward(change, consumer)
    details = {
        "backward_rule_id": backward.rule_id,
        "forward_rule_id": forward.rule_id,
        "backward_severity": backward.severity,
        "forward_severity": forward.severity,
    }
    if backward.severity == forward.severity == "PASS":
        return _outcome("PASS", "SG-COMP-300", f"full mode is compatible for {change.field_path}", **details)
    if backward.severity == forward.severity == "REVIEW":
        return _outcome("REVIEW", "SG-COMP-301", f"full mode requires review for {change.field_path}", **details)
    if backward.severity == forward.severity == "BLOCK":
        return _outcome("BLOCK", "SG-COMP-302", f"full mode blocks change at {change.field_path}", **details)
    chosen = backward if SEVERITY_RANK[backward.severity] > SEVERITY_RANK[forward.severity] else forward
    return _outcome(
        chosen.severity,
        chosen.rule_id,
        chosen.message + " (full mode evaluates both directions)",
        **details,
    )


def _base_rule(change: Change, consumer: ConsumerContract) -> RuleOutcome:
    if consumer.mode == "none":
        return _outcome(
            "PASS", "SG-COMP-400",
            f"mode none records change at {change.field_path} without blocking",
        )
    if consumer.mode == "backward":
        return _backward(change, consumer)
    if consumer.mode == "forward":
        return _forward(change, consumer)
    if consumer.mode == "full":
        return _full(change, consumer)
    raise AssertionError(f"unvalidated compatibility mode: {consumer.mode}")


def evaluate_change(consumer: ConsumerContract, policy: Policy, change: Change) -> RuleOutcome:
    """Apply policy precedence and return the final, explainable outcome."""
    if change.change_type == "partition_change":
        if "partition_change" in policy.forbidden_changes:
            return _outcome(
                "BLOCK", "SG-POL-001",
                f"policy forbids partition changes at {change.field_path}",
            )
        override = consumer.severity_overrides.get("partition_change")
        if override is not None:
            return _outcome(
                override, "SG-POL-003",
                f"consumer override sets partition_change to {override}",
                base_rule_id="SG-PART-001",
            )
        if consumer.allow_partition_change:
            return _outcome(
                "PASS", "SG-PART-002",
                f"consumer explicitly allows partition changes at {change.field_path}",
            )
        return _outcome(
            "REVIEW", "SG-PART-001",
            f"partition change requires review at {change.field_path}",
        )
    base = _base_rule(change, consumer)
    if consumer.mode == "none":
        return base
    if change.change_type in policy.forbidden_changes:
        return _outcome(
            "BLOCK", "SG-POL-001",
            f"policy forbids {change.change_type} at {change.field_path}",
            base_rule_id=base.rule_id,
            base_severity=base.severity,
        )
    override = consumer.severity_overrides.get(change.change_type)
    if override is not None and override != base.severity:
        return _outcome(
            override, "SG-POL-003",
            f"consumer override sets {change.change_type} from {base.severity} to {override}",
            base_rule_id=base.rule_id,
            base_severity=base.severity,
        )
    if base.severity == "PASS" and change.change_type in policy.review_changes:
        return _outcome(
            "REVIEW", "SG-POL-002",
            f"policy requires review for {change.change_type} at {change.field_path}",
            base_rule_id=base.rule_id,
        )
    return base


def _finding(consumer_id: str, outcome: RuleOutcome, change: Change | None = None,
             *, evidence_path: str = "", details: dict | None = None) -> Finding:
    if change is None:
        return Finding(
            consumer_id=consumer_id, severity=outcome.severity,
            rule_id=outcome.rule_id, change_id="no-change", pair_id="",
            change_type="no_change", field_path="", evidence_path=evidence_path,
            message=outcome.message, details=details or outcome.details,
        )
    return Finding(
        consumer_id=consumer_id, severity=outcome.severity, rule_id=outcome.rule_id,
        change_id=change.change_id, pair_id=change.pair_id,
        change_type=change.change_type, field_path=change.field_path,
        evidence_path=change.evidence_path, message=outcome.message,
        details=outcome.details,
    )


def affected_changes(case: CaseData, consumer: ConsumerContract,
                     changes: tuple[Change, ...]) -> tuple[Change, ...]:
    positions = {version.version: index for index, version in enumerate(case.versions)}
    anchor = max(positions[version] for version in consumer.declared_versions)
    return tuple(c for c in changes if positions[c.from_version] >= anchor)


def evaluate_case(case: CaseData, changes: tuple[Change, ...]) -> tuple[ConsumerVerdict, ...]:
    positions = {version.version: index for index, version in enumerate(case.versions)}
    verdicts = []
    for consumer in sorted(case.consumers, key=lambda item: item.consumer_id):
        anchor_index = max(positions[v] for v in consumer.declared_versions)
        anchor_version = case.versions[anchor_index].version
        findings = [
            _finding(consumer.consumer_id, evaluate_change(consumer, case.policy, change),
                     change)
            for change in affected_changes(case, consumer, changes)
        ]
        if not findings:
            findings.append(_finding(
                consumer.consumer_id,
                _outcome("PASS", "SG-COMP-000",
                         "no pending schema changes for the consumer's declared versions"),
                evidence_path=f"cases.{case.case_id}.consumers.{consumer.consumer_id}",
                details={"anchor_version": anchor_version},
            ))
        decision = max((item.severity for item in findings),
                       key=lambda value: SEVERITY_RANK[value], default="PASS")
        verdicts.append(ConsumerVerdict(
            consumer_id=consumer.consumer_id, mode=consumer.mode,
            anchor_version=anchor_version, decision=decision,
            findings=tuple(findings), read_versions=consumer.read_versions,
            write_versions=consumer.write_versions,
        ))
    return tuple(verdicts)


# ---------------------------------------------------------------------------
# Rollback retain-field support
#
# A rollback to an older schema version must keep the field paths that the
# rolled-back consumers still read or write, including every path a logical
# field has carried across versions (explicit renames, drop/re-add with the
# same field_id or aliases, and struct child-path reorganisation).  The helpers
# below build a cross-version field-identity index first, then answer two
# questions for a given rollback point:
#   1. which paths do the declared consumers touch at the rollback version and
#      any later version they still read or write;
#   2. which identities disappeared at the rollback point but flow back in a
#      later version under the same name, field_id or alias.
# ---------------------------------------------------------------------------


class FieldIdentity:
    """Union-find over every ``(version, field_path)`` occurrence.

    Two occurrences belong to the same identity when they are the same path in
    different versions, share a ``field_id``, cross-reference through
    ``aliases``, or are explicitly joined by a ``rename_field`` change.  The
    construction is deterministic: every link is driven by sorted inputs.
    """

    def __init__(self, case: CaseData, changes: tuple[Change, ...] = ()):
        positions = {version.version: index for index, version in enumerate(case.versions)}
        self._positions = positions
        nodes: set[tuple[str, str]] = set()
        by_id: dict[int, list[tuple[str, FieldSpec]]] = {}
        by_alias: dict[str, list[tuple[str, FieldSpec]]] = {}
        for version in case.versions:
            for item in version.field_index().values():
                node = (version.version, item.path)
                nodes.add(node)
                if item.field_id is not None:
                    by_id.setdefault(item.field_id, []).append((version.version, item))
                for alias in item.aliases:
                    by_alias.setdefault(alias, []).append((version.version, item))
        self._parent: dict[tuple[str, str], tuple[str, str]] = {
            node: node for node in nodes
        }

        def find(node: tuple[str, str]) -> tuple[str, str]:
            root = node
            while self._parent[root] != root:
                root = self._parent[root]
            while self._parent[node] != node:
                parent = self._parent[node]
                self._parent[node] = root
                node = parent
            return root

        def union(left: tuple[str, str], right: tuple[str, str]) -> None:
            left_root, right_root = find(left), find(right)
            if left_root == right_root:
                return
            # Tie-break on (version position, path) so the chosen root never
            # depends on set or dict iteration order.
            chosen = min(left_root, right_root,
                         key=lambda node: (positions[node[0]], node[1]))
            other = right_root if chosen == left_root else left_root
            self._parent[other] = chosen

        self._find = find
        self._union = union
        # Same path only links *adjacent* versions: a name that disappears for
        # a version must not silently merge its before/after occurrences.  A
        # same-name reflow is instead re-linked through field_id or aliases
        # below, which carry the identity evidence across the gap.
        for index in range(len(case.versions) - 1):
            old_index = case.versions[index].field_index()
            new_index = case.versions[index + 1].field_index()
            for path, new_item in sorted(new_index.items()):
                old_item = old_index.get(path)
                if old_item is not None:
                    union((case.versions[index].version, path),
                          (case.versions[index + 1].version, new_item.path))
        # Same field_id across any versions, including drop/re-add reflow and
        # struct children rehomed under a renamed parent struct.
        for occurrences in by_id.values():
            self._link_occurrences(occurrences)
        # Alias cross-references in both directions: a later field name may be
        # listed as an alias of the earlier one and vice versa.  This also
        # bridges a drop/re-add gap when the reflowed field keeps the alias.
        for occurrences in by_alias.values():
            self._link_occurrences(occurrences)
        # Explicit rename evidence supplied by the diff engine; this is the
        # authoritative link when names, ids or aliases alone are ambiguous.
        for change in sorted(changes, key=lambda item: item.change_id):
            if change.change_type != "rename_field":
                continue
            old_path = change.details.get("old_path")
            new_path = change.details.get("new_path")
            if isinstance(old_path, str) and isinstance(new_path, str):
                old_node = (change.from_version, old_path)
                new_node = (change.to_version, new_path)
                if old_node in self._parent and new_node in self._parent:
                    union(old_node, new_node)

    def _link_occurrences(self, occurrences: list[tuple[str, FieldSpec]]) -> None:
        ordered = sorted(occurrences, key=lambda item: (self._positions[item[0]], item[1].path))
        anchor = ordered[0]
        anchor_node = (anchor[0], anchor[1].path)
        for version, item in ordered[1:]:
            self._union(anchor_node, (version, item.path))

    def equivalence_paths(self, version: str, path: str) -> tuple[str, ...]:
        """All paths the identity of ``(version, path)`` uses in any version."""
        seed = (version, path)
        if seed not in self._parent:
            return (path,)
        root = self._find(seed)
        matched = sorted(
            node[1]
            for node in self._parent
            if self._find(node) == root
        )
        return tuple(dict.fromkeys(matched))

    def identity_members(
        self,
    ) -> tuple[tuple[tuple[str, str], ...], ...]:
        """Versioned ``(version, path)`` occurrences grouped by identity.

        Groups and members are ordered by version position and path so callers
        can scan them without introducing nondeterministic iteration.
        """
        grouped: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for node in sorted(self._parent,
                           key=lambda item: (self._positions[item[0]], item[1])):
            grouped.setdefault(self._find(node), []).append(node)
        members = tuple(tuple(nodes) for nodes in grouped.values())
        return tuple(sorted(members, key=lambda nodes: tuple(
            (self._positions[node[0]], node[1]) for node in nodes
        )))


def build_field_identity(case: CaseData,
                         changes: tuple[Change, ...] = ()) -> FieldIdentity:
    """Index logical field identities across all versions of ``case``."""
    return FieldIdentity(case, changes)


def _version_index(case: CaseData) -> dict[str, SchemaVersion]:
    return {version.version: version for version in case.versions}


def consumer_field_paths(case: CaseData, consumer: ConsumerContract,
                         rollback_version: str) -> tuple[str, ...]:
    """Flat field paths (including struct children) the consumer touches.

    Only declared read/write versions at or after the rollback point count:
    those are the schemas the consumer must keep working against while older
    compatibility declarations are re-enabled.
    """
    positions = {version.version: index for index, version in enumerate(case.versions)}
    if rollback_version not in positions:
        return ()
    floor = positions[rollback_version]
    versions_by_name = _version_index(case)
    paths: set[str] = set()
    for version_name in sorted(
        {*consumer.read_versions, *consumer.write_versions},
        key=lambda name: positions[name],
    ):
        if positions[version_name] < floor:
            continue
        paths.update(versions_by_name[version_name].field_index())
    return tuple(sorted(paths))


def consumer_rollback_versions(
    case: CaseData, consumer_ids: tuple[str, ...], rollback_version: str
) -> dict[str, dict[str, list[str]]]:
    """Per-consumer declared read/write versions at or after the rollback point.

    The returned mapping also carries the flat field paths those versions
    touch; it drives the ``consumer_versions`` block of a rollback hint and is
    always evaluated against the hint's own rollback point so merging per-pair
    payloads with different floors cannot silently overwrite a consumer.
    """
    positions = {version.version: index for index, version in enumerate(case.versions)}
    consumers_by_id = {item.consumer_id: item for item in case.consumers}
    payload: dict[str, dict[str, list[str]]] = {}
    if rollback_version not in positions:
        return payload
    for consumer_id in sorted(set(consumer_ids)):
        consumer = consumers_by_id.get(consumer_id)
        if consumer is None:
            continue
        floor = positions[rollback_version]
        payload[consumer_id] = {
            "read_versions": [
                name for name in sorted(consumer.read_versions, key=positions.get)
                if positions[name] >= floor
            ],
            "write_versions": [
                name for name in sorted(consumer.write_versions, key=positions.get)
                if positions[name] >= floor
            ],
            "field_paths": list(
                consumer_field_paths(case, consumer, rollback_version)
            ),
        }
    return payload


def rollback_retain_plan(
    case: CaseData,
    rollback_version: str,
    consumer_ids: tuple[str, ...],
    identity: FieldIdentity,
) -> dict[str, object]:
    """Compute the retain set and reuse annotations for one rollback hint.

    The base retain set holds every path used by the declared consumers at the
    rollback point and later versions, expanded so that all alias paths of a
    touched identity (renames, field_id reflow, struct reorganisation) are kept.

    A logical field that exists before the rollback point, is absent from the
    rollback-point schema, and reappears in a later version under the same
    name, field_id or aliases is additionally retained and annotated with
    ``reuse_across_rollback``.
    """
    positions = {version.version: index for index, version in enumerate(case.versions)}
    if rollback_version not in positions:
        return {
            "rollback_point": rollback_version,
            "consumer_versions": {},
            "retain_fields": [],
            "reuse_across_rollback": [],
        }
    floor = positions[rollback_version]
    versions_by_name = _version_index(case)
    ordered_ids = tuple(sorted(set(consumer_ids)))

    consumer_versions = consumer_rollback_versions(
        case, ordered_ids, rollback_version
    )
    base_paths: set[str] = set()
    for payload in consumer_versions.values():
        base_paths.update(payload["field_paths"])

    retained: set[str] = set()
    for path in base_paths:
        retained.update(identity.equivalence_paths(rollback_version, path))

    pair_labels = [
        f"{case.versions[index].version}->{case.versions[index + 1].version}"
        for index in range(len(case.versions) - 1)
    ]
    reuse_entries: list[dict[str, object]] = []
    for members in identity.identity_members():
        # Actual (version position -> path) occurrences of this identity.
        present: dict[int, str] = {}
        field_id_seen: int | None = None
        for version_name, path in members:
            present[positions[version_name]] = path
            item = versions_by_name[version_name].field_index().get(path)
            if item is not None and field_id_seen is None and item.field_id is not None:
                field_id_seen = item.field_id
        if floor in present:
            continue  # present at the rollback point -> covered by the base set
        if not any(index < floor for index in present):
            continue  # never existed before the rollback point
        if not any(index > floor for index in present):
            continue  # does not flow back after the rollback point
        # Every adjacent transition where this identity appears or vanishes;
        # around a reflow those are the drop pair and the re-add pair.
        source_pairs = sorted({
            pair_labels[index]
            for index in range(len(case.versions) - 1)
            if (index in present) != (index + 1 in present)
        })
        group_paths = sorted(set(present.values()))
        retained.update(group_paths)
        reuse_entries.append({
            "kind": "reuse_across_rollback",
            "retained_paths": group_paths,
            "field_id": field_id_seen,
            "consumer_ids": list(ordered_ids),
            "source_pairs": source_pairs,
        })
    reuse_entries.sort(
        key=lambda entry: (entry["retained_paths"], entry["source_pairs"])
    )
    return {
        "rollback_point": rollback_version,
        "consumer_versions": consumer_versions,
        "retain_fields": sorted(retained),
        "reuse_across_rollback": reuse_entries,
    }


def merge_reuse_annotations(
    entries: tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    """Merge per-pair ``reuse_across_rollback`` entries for the final hint.

    Entries describing the same retained path set are folded together: the
    consumer ids and source pairs are unioned while the field_id is taken from
    whichever entry carries one.  Output ordering stays deterministic.
    """
    merged: dict[tuple[str, ...], dict[str, object]] = {}
    for entry in entries:
        key = tuple(entry.get("retained_paths", ()))  # type: ignore[arg-type]
        target = merged.get(key)
        if target is None:
            merged[key] = {
                "kind": "reuse_across_rollback",
                "retained_paths": list(key),
                "field_id": entry.get("field_id"),
                "consumer_ids": sorted(entry.get("consumer_ids", ())),
                "source_pairs": sorted(entry.get("source_pairs", ())),
            }
            continue
        if target["field_id"] is None:
            target["field_id"] = entry.get("field_id")
        target["consumer_ids"] = sorted(
            set(target["consumer_ids"]) | set(entry.get("consumer_ids", ()))
        )
        target["source_pairs"] = sorted(
            set(target["source_pairs"]) | set(entry.get("source_pairs", ()))
        )
    return sorted(
        merged.values(),
        key=lambda item: (item["retained_paths"], item["source_pairs"]),
    )
