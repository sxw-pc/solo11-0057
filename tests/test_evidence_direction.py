"""Evidence direction and version: model regulation, diff emission, full mode.

The regulated contract under test:

* destructive field facts are ``old`` and point at the from-version,
* additive field facts are ``new`` and point at the to-version,
* renames and field-order changes are ``both``,
* ``struct_field_change`` derives its direction from grouped child evidence,
* full mode consults only the branch that can carry the change's evidence,
* an uninterpretable direction is REVIEW rather than a silent PASS/BLOCK.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from schemagate.compatibility import evaluate_change
from schemagate.config import CHANGE_TYPES
from schemagate.models import (
    CHANGE_EVIDENCE_DIRECTIONS,
    EVIDENCE_DIRECTIONS,
    evidence_version_for,
)
from tests.helpers import diff_docs, field, make_change, make_consumer


def _policy():
    from schemagate.models import Policy

    return Policy(policy_id="test", default_mode="full")


def _diff(old_fields, new_fields):
    return diff_docs(
        {"table": "demo.unit", "version": "v1", "fields": old_fields},
        {"table": "demo.unit", "version": "v2", "fields": new_fields},
    )


def _only(changes, change_type):
    matched = [change for change in changes if change.change_type == change_type]
    assert len(matched) == 1, (change_type, [item.change_type for item in changes])
    return matched[0]


# --- regulated direction table -------------------------------------------


def test_every_change_type_declares_a_direction_rule():
    assert set(CHANGE_EVIDENCE_DIRECTIONS) == set(CHANGE_TYPES)
    for direction in CHANGE_EVIDENCE_DIRECTIONS.values():
        assert direction is None or direction in EVIDENCE_DIRECTIONS


def test_evidence_version_follows_direction():
    assert evidence_version_for("old", "v1", "v2") == "v1"
    assert evidence_version_for("new", "v1", "v2") == "v2"
    assert evidence_version_for("both", "v1", "v2") == "v2"
    with pytest.raises(ValueError):
        evidence_version_for("sideways", "v1", "v2")


# --- per-change-type emission -------------------------------------------


def test_drop_is_old_evidence():
    change = _only(_diff([field("id", "string"), field("legacy", "string")],
                         [field("id", "string")]), "drop_field")
    assert change.evidence_direction == "old"
    assert change.evidence_version == "v1"
    assert ".versions.v1." in change.evidence_path


def test_add_is_new_evidence():
    change = _only(_diff([field("id", "string")],
                         [field("id", "string"), field("fresh", "string")]),
                   "add_field")
    assert change.evidence_direction == "new"
    assert change.evidence_version == "v2"
    assert ".versions.v2." in change.evidence_path


def test_rename_is_both_and_keeps_both_paths():
    change = _only(_diff([field("old_name", "string", field_id=7)],
                         [field("new_name", "string", field_id=7)]), "rename_field")
    assert change.evidence_direction == "both"
    assert change.evidence_version == "v2"
    assert change.details["old_path"] == "old_name"
    assert change.details["new_path"] == "new_name"


def test_field_order_is_both_evidence():
    change = _only(_diff(
        [field("alpha", "string"), field("beta", "string")],
        [field("beta", "string"), field("alpha", "string")],
    ), "field_order_change")
    assert change.evidence_direction == "both"
    assert change.evidence_version == "v2"
    assert change.details["old_order"] == ["alpha", "beta"]
    assert change.details["new_order"] == ["beta", "alpha"]


def test_compare_each_side_change_is_both_evidence():
    changes = _diff([field("value", "int32")], [field("value", "int64")])
    change = _only(changes, "widen_type")
    assert (change.evidence_direction, change.evidence_version) == ("both", "v2")


# --- struct aggregate derivation ----------------------------------------


def test_struct_aggregate_is_new_when_only_children_are_added():
    changes = _diff(
        [field("payload", "struct", fields=[field("action", "string")])],
        [field("payload", "struct", fields=[
            field("action", "string"), field("channel", "string"),
        ])],
    )
    aggregate = _only(changes, "struct_field_change")
    assert aggregate.evidence_direction == "new"
    assert aggregate.evidence_version == "v2"
    assert aggregate.details["changed_paths"] == {"new": ["payload.channel"]}


def test_struct_aggregate_is_old_when_only_children_are_dropped():
    changes = _diff(
        [field("payload", "struct", fields=[
            field("action", "string"), field("device", "string"),
        ])],
        [field("payload", "struct", fields=[field("action", "string")])],
    )
    aggregate = _only(changes, "struct_field_change")
    assert aggregate.evidence_direction == "old"
    assert aggregate.evidence_version == "v1"
    assert aggregate.details["changed_paths"] == {"old": ["payload.device"]}


def test_struct_aggregate_is_both_when_children_drop_and_add():
    changes = _diff(
        [field("payload", "struct", fields=[
            field("action", "string"), field("device", "string"),
        ])],
        [field("payload", "struct", fields=[
            field("action", "string"), field("channel", "string"),
        ])],
    )
    aggregate = _only(changes, "struct_field_change")
    assert aggregate.evidence_direction == "both"
    # "both" aggregates point at the current (to-version) structure.
    assert aggregate.evidence_version == "v2"
    assert aggregate.details["changed_paths"] == {
        "old": ["payload.device"],
        "new": ["payload.channel"],
    }


def test_struct_aggregate_ignores_non_struct_siblings():
    changes = _diff(
        [field("id", "string"), field("payload", "struct", fields=[field("a", "string")])],
        [field("id", "string"), field("payload", "struct", fields=[
            field("a", "string"), field("b", "string"),
        ])],
    )
    aggregate = _only(changes, "struct_field_change")
    assert aggregate.details["changed_paths"] == {"new": ["payload.b"]}


# --- full mode branch selection -----------------------------------------


def test_full_backward_ignores_new_only_evidence():
    change = make_change("add_field", details={"required": True})
    assert change.evidence_direction == "new"
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    # The backward branch would block with SG-COMP-101, but it cannot see a
    # fact that is only declared in the to-version.
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-COMP-201"
    assert outcome.details["backward_applicability"] == "not-applicable"
    assert outcome.details["forward_applicability"] == "evaluated"


def test_full_forward_ignores_old_only_evidence():
    change = make_change("drop_field")
    assert change.evidence_direction == "old"
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-COMP-103"
    assert outcome.details["backward_applicability"] == "evaluated"
    assert outcome.details["forward_applicability"] == "not-applicable"


def test_full_evaluates_both_sides_for_both_evidence():
    change = make_change("nullable_to_true")
    assert change.evidence_direction == "both"
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "PASS"
    assert outcome.rule_id == "SG-COMP-300"
    assert outcome.details["backward_applicability"] == "evaluated"
    assert outcome.details["forward_applicability"] == "evaluated"


def test_full_struct_both_evidence_uses_both_branches():
    changes = _diff(
        [field("payload", "struct", fields=[
            field("action", "string"), field("device", "string"),
        ])],
        [field("payload", "struct", fields=[
            field("action", "string"), field("channel", "string"),
        ])],
    )
    aggregate = _only(changes, "struct_field_change")
    outcome = evaluate_change(make_consumer("full"), _policy(), aggregate)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-301"


def test_full_struct_old_only_evidence_uses_backward_rule():
    changes = _diff(
        [field("payload", "struct", fields=[
            field("action", "string"), field("device", "string"),
        ])],
        [field("payload", "struct", fields=[field("action", "string")])],
    )
    aggregate = _only(changes, "struct_field_change")
    outcome = evaluate_change(make_consumer("full"), _policy(), aggregate)
    assert outcome.rule_id == "SG-COMP-118"
    assert outcome.details["forward_applicability"] == "not-applicable"


@pytest.mark.parametrize("direction", ["", "sideways", None])
def test_full_uninterpretable_direction_is_review(direction):
    base = make_change("add_field", evidence_direction="new")
    change = replace(base, evidence_direction=direction)
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-304"
    assert outcome.details["backward_applicability"] == "direction-uninterpretable"
    assert outcome.details["forward_applicability"] == "direction-uninterpretable"


def test_full_direction_version_mismatch_is_review():
    change = make_change(
        "drop_field", evidence_direction="old", evidence_version="v2"
    )
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-304"
    assert outcome.details["reason"] == "unknown evidence direction"


def test_full_struct_grouping_mismatch_is_review():
    change = make_change(
        "struct_field_change",
        evidence_direction="new",
        details={"changed_paths": {"old": ["payload.device"]}},
    )
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-304"
    assert "changed_paths" in outcome.details["reason"]


def test_full_struct_missing_grouping_is_review():
    change = make_change(
        "struct_field_change", evidence_direction="both", details={"changed_paths": []}
    )
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-304"


def test_non_full_modes_are_unaffected_by_direction_filtering():
    # A forward consumer still rejects a drop even though its evidence is old.
    outcome = evaluate_change(
        make_consumer("forward", read_versions=(), write_versions=("v1",)),
        _policy(),
        make_change("drop_field"),
    )
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-COMP-203"


def test_full_accepts_direction_without_explicit_version():
    change = replace(
        make_change("drop_field", evidence_direction="old"), evidence_version=""
    )
    outcome = evaluate_change(make_consumer("full"), _policy(), change)
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-COMP-103"
