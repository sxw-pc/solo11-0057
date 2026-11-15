from __future__ import annotations

from schemagate.compatibility import evaluate_case, evaluate_change
from schemagate.models import ConstraintAcceptance, Policy
from schemagate.type_rules import (
    aggregate_directions,
    compare_constraint,
    compare_constraints,
)
from tests.helpers import diff_docs, field, make_change, make_consumer


def _policy(**kwargs):
    return Policy(
        policy_id="test",
        default_mode=kwargs.pop("default_mode", "backward"),
        forbidden_changes=tuple(kwargs.pop("forbidden", ())),
        review_changes=tuple(kwargs.pop("review", ())),
    )


def _diff(old_fields, new_fields):
    return diff_docs(
        {"table": "demo.unit", "version": "v1", "fields": old_fields},
        {"table": "demo.unit", "version": "v2", "fields": new_fields},
    )


def _typed(changes, change_type):
    return [change for change in changes if change.change_type == change_type]


# ---------------------------------------------------------------------------
# Direction classification
# ---------------------------------------------------------------------------

def test_numeric_bounds_classify_tightening_and_relaxation():
    assert compare_constraint("max", 100, 50) == "narrow"
    assert compare_constraint("max", 50, 100) == "widen"
    assert compare_constraint("min", 0, 5) == "narrow"
    assert compare_constraint("min", 5, 0) == "widen"
    assert compare_constraint("exclusive_max", 10, 20) == "widen"
    assert compare_constraint("max", 100, 100) == "widen"


def test_length_constraints_classify_tightening_and_relaxation():
    assert compare_constraint("max_length", 10, 20) == "widen"
    assert compare_constraint("max_length", 20, 10) == "narrow"
    assert compare_constraint("min_length", 3, 1) == "widen"
    assert compare_constraint("min_length", 1, 3) == "narrow"
    assert compare_constraint("max_items", 5, 9) == "widen"
    # exact_length admits exactly one size, so a changed size is disjoint.
    assert compare_constraint("exact_length", 8, 10) == "incomparable"
    assert compare_constraint("exact_length", 10, 10) == "widen"


def test_enum_value_sets_classify_growth_and_shrinkage():
    assert compare_constraint("enum", ["a", "b"], ["a", "b", "c"]) == "widen"
    assert compare_constraint("values", ["a", "b", "c"], ["a", "b"]) == "narrow"
    assert compare_constraint("values", ["a", "b"], ["b", "a"]) == "widen"


def test_disjoint_or_overlapping_enums_are_incomparable():
    assert compare_constraint("enum", ["a", "b"], ["b", "c"]) == "incomparable"
    assert compare_constraint("enum", ["a"], ["b"]) == "incomparable"


def test_pattern_and_charset_changes_are_incomparable():
    assert compare_constraint("pattern", "^a$", "^b$") == "incomparable"
    assert compare_constraint("pattern", "^a$", "^a$") == "widen"
    assert compare_constraint("charset", "ascii", "utf-8") == "incomparable"
    assert compare_constraint("charset", "utf-8", "utf-8") == "widen"


def test_unordered_and_unparsable_changes_fall_back_to_incomparable():
    # A renamed/unknown constraint family has no comparable order.
    assert compare_constraint("opaque_tag", {"a": 1}, {"b": 2}) == "incomparable"
    # Booleans are not numeric bounds.
    assert compare_constraint("max", True, False) == "incomparable"


def test_aggregate_directions_takes_the_most_conservative_value():
    entries = (
        {"direction": "widen"},
        {"direction": "widen"},
    )
    assert aggregate_directions(entries) == "widen"
    entries = (
        {"direction": "widen"},
        {"direction": "incomparable"},
    )
    assert aggregate_directions(entries) == "incomparable"
    entries = (
        {"direction": "incomparable"},
        {"direction": "narrow"},
    )
    assert aggregate_directions(entries) == "narrow"


def test_adding_and_removing_bounds_classify_correctly():
    entries = {item["constraint"]: item for item in compare_constraints({}, {"max": 50})}
    assert entries["max"]["change"] == "added"
    assert entries["max"]["direction"] == "narrow"
    entries = {item["constraint"]: item for item in compare_constraints({"max": 50}, {})}
    assert entries["max"]["change"] == "removed"
    assert entries["max"]["direction"] == "widen"


# ---------------------------------------------------------------------------
# Diff engine emission and aggregation
# ---------------------------------------------------------------------------

def test_constraint_change_carries_direction_and_concrete_values():
    changes = _diff(
        [field("amount", "decimal(10,2)", constraints={"max": 100})],
        [field("amount", "decimal(10,2)", constraints={"max": 50})],
    )
    change = _typed(changes, "constraint_change")[0]
    assert change.field_path == "amount"
    assert change.details["direction"] == "narrow"
    assert change.details["constraint_changes"][0]["constraint"] == "max"
    assert change.details["constraint_changes"][0]["old_value"] == 100
    assert change.details["constraint_changes"][0]["new_value"] == 50
    assert "narrow" in change.summary


def test_mixed_constraints_per_field_are_incomparable():
    changes = _diff(
        [field("amount", "decimal(10,2)", constraints={"min": 0, "max": 100})],
        [field("amount", "decimal(10,2)", constraints={"min": 0, "max": 50})],
    )
    assert _typed(changes, "constraint_change")[0].details["direction"] == "narrow"


def test_struct_aggregate_keeps_parent_and_child_paths_and_excludes_generic_change():
    changes = _diff(
        [field("price_details", "struct", fields=[
            field("amount", "decimal(10,2)", constraints={"max": 100}),
            field("currency", "string", constraints={"values": ["a", "b"]}),
        ])],
        [field("price_details", "struct", fields=[
            field("amount", "decimal(10,2)", constraints={"max": 50}),
            field("currency", "string", constraints={"values": ["a", "b", "c"]}),
        ])],
    )
    aggregates = _typed(changes, "constraint_change")
    struct_aggregate = next(
        item for item in aggregates if item.field_path == "price_details"
    )
    assert struct_aggregate.details["parent_path"] == "price_details"
    assert struct_aggregate.details["changed_paths"] == [
        "price_details.amount", "price_details.currency"
    ]
    assert struct_aggregate.details["direction"] == "narrow"
    child_directions = {
        item["path"]: item["direction"]
        for item in struct_aggregate.details["children"]
    }
    assert child_directions == {
        "price_details.amount": "narrow",
        "price_details.currency": "widen",
    }
    assert not _typed(changes, "struct_field_change")
    assert _typed(changes, "constraint_change")[1].field_path == "price_details.amount"


def test_nested_struct_with_non_constraint_change_still_gets_struct_change():
    changes = _diff(
        [field("price_details", "struct", fields=[
            field("amount", "decimal(10,2)", constraints={"max": 100}),
            field("currency", "string", constraints={"values": ["a", "b"]}),
        ])],
        [field("price_details", "struct", fields=[
            field("amount", "decimal(10,2)", constraints={"max": 50}),
            field("currency", "string", constraints={"values": ["a", "b", "c"]}),
            field("channel", "string"),
        ])],
    )
    aggregate = _typed(changes, "struct_field_change")[0]
    assert aggregate.field_path == "price_details"
    # The generic aggregate only lists the structural change, not the
    # constraint paths it must not mask.
    assert aggregate.details["changed_paths"] == ["price_details.channel"]


def test_struct_parent_constraint_and_child_constraint_share_one_aggregate():
    changes = _diff(
        [field("payload", "struct", constraints={"max_length": 4}, fields=[
            field("action", "string", constraints={"values": ["a"]}),
        ])],
        [field("payload", "struct", constraints={"max_length": 8}, fields=[
            field("action", "string", constraints={"values": ["a", "b"]}),
        ])],
    )
    aggregates = [
        item for item in _typed(changes, "constraint_change")
        if item.field_path == "payload"
    ]
    assert len(aggregates) == 1
    aggregate = aggregates[0]
    assert aggregate.details["changed_paths"] == ["payload", "payload.action"]
    assert aggregate.details["direction"] == "widen"


# ---------------------------------------------------------------------------
# Compatibility grading per consumer path
# ---------------------------------------------------------------------------

def _constraint_change(path="price_details", children=None):
    details = {"direction": "narrow"}
    if children is not None:
        details["children"] = children
        details["changed_paths"] = [child["path"] for child in children]
        details["direction"] = max(
            (child["direction"] for child in children),
            key=lambda item: {"widen": 0, "incomparable": 1, "narrow": 2}[item],
        )
    return make_change("constraint_change", path=path, details=details)


def _accepting_consumer(**acceptance):
    return make_consumer(
        "backward",
        acceptance=tuple(
            ConstraintAcceptance(path=path, accepted=tuple(dirs))
            for path, dirs in acceptance.items()
        ),
    )


def test_declared_accepted_direction_passes():
    consumer = _accepting_consumer(**{"price_details.currency": ["widen"]})
    outcome = evaluate_change(
        consumer,
        _policy(),
        _constraint_change(
            "price_details.currency", children=[{
                "path": "price_details.currency",
                "direction": "widen",
            }],
        ),
    )
    assert outcome.severity == "PASS"
    assert outcome.rule_id == "SG-COMP-120"


def test_declared_unaccepted_direction_blocks():
    consumer = _accepting_consumer(**{"price_details.amount": ["widen"]})
    change = _constraint_change(
        "price_details.amount", children=[{
            "path": "price_details.amount", "direction": "narrow",
        }],
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-COMP-121"


def test_undeclared_path_keeps_review():
    consumer = _accepting_consumer(**{"price_details.amount": ["widen"]})
    change = _constraint_change(
        "other.path", children=[{"path": "other.path", "direction": "widen"}]
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-119"


def test_struct_aggregate_takes_the_most_severe_child():
    consumer = _accepting_consumer(
        **{
            "price_details.amount": ["widen"],
            "price_details.currency": ["widen"],
        }
    )
    change = _constraint_change(
        "price_details",
        children=[
            {"path": "price_details.amount", "direction": "narrow"},
            {"path": "price_details.currency", "direction": "widen"},
        ],
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "BLOCK"


def test_struct_aggregate_all_accepted_passes():
    consumer = _accepting_consumer(
        **{
            "price_details.amount": ["narrow", "widen"],
            "price_details.currency": ["widen"],
        }
    )
    change = _constraint_change(
        "price_details",
        children=[
            {"path": "price_details.amount", "direction": "narrow"},
            {"path": "price_details.currency", "direction": "widen"},
        ],
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "PASS"


def test_sibling_declarations_do_not_leak_between_paths():
    consumer = _accepting_consumer(
        **{
            "price_details.amount": ["widen"],
            "price_details.currency": ["widen"],
        }
    )
    change = _constraint_change(
        "price_details",
        children=[
            {"path": "price_details.amount", "direction": "widen"},
            {"path": "price_details.other", "direction": "widen"},
        ],
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-COMP-119"


def test_ancestor_declaration_applies_to_descendant_path():
    consumer = _accepting_consumer(**{"price_details": ["widen"]})
    change = _constraint_change(
        "price_details.currency",
        children=[{"path": "price_details.currency", "direction": "widen"}],
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "PASS"


def test_most_specific_declaration_wins_over_ancestor():
    consumer = _accepting_consumer(
        **{
            "price_details": ["widen", "narrow"],
            "price_details.amount": ["widen"],
        }
    )
    change = _constraint_change(
        "price_details.amount",
        children=[{"path": "price_details.amount", "direction": "narrow"}],
    )
    outcome = evaluate_change(consumer, _policy(), change)
    assert outcome.severity == "BLOCK"


def test_incomparable_change_requires_explicit_acceptance():
    consumer = _accepting_consumer(**{"blob.pattern": ["widen"]})
    change = _constraint_change(
        "blob.pattern",
        children=[{"path": "blob.pattern", "direction": "incomparable"}],
    )
    assert evaluate_change(consumer, _policy(), change).severity == "BLOCK"
    accepting = _accepting_consumer(**{"blob.pattern": ["incomparable"]})
    assert evaluate_change(accepting, _policy(), change).severity == "PASS"


# ---------------------------------------------------------------------------
# End-to-end fixture case
# ---------------------------------------------------------------------------

def test_lake_pricing_fixture_reproduces_widen_and_narrow():
    from schemagate.loader import CaseLoader
    from schemagate.runner import build_result

    case = CaseLoader("fixtures/cases").load_case("lake_pricing")
    result = build_result(case)
    changes = {
        (item["change_type"], item["field_path"]): item
        for item in result["changes"]
    }
    assert changes[("constraint_change", "price_details.amount")]["details"]["direction"] == "narrow"
    assert changes[("constraint_change", "price_details.currency")]["details"]["direction"] == "widen"
    aggregates = [
        item for item in result["changes"]
        if item["change_type"] == "constraint_change"
        and item["field_path"] == "price_details"
    ]
    assert len(aggregates) == 1
    assert aggregates[0]["details"]["direction"] == "narrow"
    assert set(aggregates[0]["details"]["changed_paths"]) == {
        "price_details.amount", "price_details.currency"
    }
    assert not any(
        item["change_type"] == "struct_field_change" for item in result["changes"]
    )

    bi_reader = next(
        item for item in result["consumers"] if item["consumer_id"] == "bi_reader"
    )
    finding_index = {(item["change_type"], item["field_path"]): item
                     for item in bi_reader["findings"]}
    amount = finding_index[("constraint_change", "price_details.amount")]
    currency = finding_index[("constraint_change", "price_details.currency")]
    aggregate = finding_index[("constraint_change", "price_details")]
    assert amount["severity"] == "BLOCK"
    assert amount["rule_id"] == "SG-COMP-121"
    assert currency["severity"] == "PASS"
    assert currency["rule_id"] == "SG-COMP-120"
    assert aggregate["severity"] == "BLOCK"

    audit_reader = next(
        item for item in result["consumers"] if item["consumer_id"] == "audit_reader"
    )
    audit_findings = [
        item for item in audit_reader["findings"]
        if item["change_type"] == "constraint_change"
    ]
    assert audit_findings
    assert all(item["severity"] == "REVIEW" for item in audit_findings)


# ---------------------------------------------------------------------------
# Contract parsing
# ---------------------------------------------------------------------------

def test_consumer_parser_accepts_path_declarations():
    from schemagate.contracts_parser import parse_consumer

    consumer = parse_consumer(
        {
            "consumer_id": "reader",
            "table": "demo.unit",
            "read_versions": ["v1"],
            "constraint_acceptance": {
                "price_details.amount": {"accepts": ["widen"]},
                "price_details.currency": ["widen", "narrow"],
            },
        },
        expected_table="demo.unit",
        known_versions=("v1", "v2"),
        default_mode="backward",
    )
    by_path = {item.path: item.accepted for item in consumer.constraint_acceptance}
    assert by_path == {
        "price_details.amount": ("widen",),
        "price_details.currency": ("narrow", "widen"),
    }


def test_consumer_parser_rejects_unknown_direction_and_bad_path():
    import pytest

    from schemagate.contracts_parser import parse_consumer
    from schemagate.errors import SchemaGateError

    base = {
        "consumer_id": "reader",
        "table": "demo.unit",
        "read_versions": ["v1"],
    }
    with pytest.raises(SchemaGateError) as caught:
        parse_consumer(
            {**base, "constraint_acceptance": {"a.b": {"accepts": ["sideways"]}}},
            expected_table="demo.unit",
            known_versions=("v1",),
            default_mode="backward",
        )
    assert caught.value.code == "SG-LOAD-010"
    with pytest.raises(SchemaGateError) as caught:
        parse_consumer(
            {**base, "constraint_acceptance": {"a..b": ["widen"]}},
            expected_table="demo.unit",
            known_versions=("v1",),
            default_mode="backward",
        )
    assert caught.value.code == "SG-LOAD-010"
    with pytest.raises(SchemaGateError) as caught:
        parse_consumer(
            {**base, "constraint_acceptance": {"a.b": {"accepts": ["widen"], "extra": 1}}},
            expected_table="demo.unit",
            known_versions=("v1",),
            default_mode="backward",
        )
    assert caught.value.code == "SG-LOAD-010"


def test_constraint_acceptance_changes_the_input_fingerprint(copied_case):
    from schemagate.loader import CaseLoader
    from tests.helpers import dump_yaml

    import yaml

    fixtures, case_dir = copied_case("lake_orders")
    before = CaseLoader(fixtures).load_case("lake_orders")
    path = case_dir / "consumers" / "finance_reader.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["constraint_acceptance"] = {"total_amount": ["widen"]}
    dump_yaml(path, document)
    after = CaseLoader(fixtures).load_case("lake_orders")
    assert before.input_fingerprint != after.input_fingerprint
