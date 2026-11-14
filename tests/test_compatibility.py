from __future__ import annotations

from schemagate.compatibility import evaluate_case, evaluate_change
from schemagate.models import Policy
from tests.helpers import make_change, make_consumer


def _policy(**kwargs):
    return Policy(
        policy_id="test",
        default_mode=kwargs.pop("default_mode", "backward"),
        forbidden_changes=tuple(kwargs.pop("forbidden", ())),
        review_changes=tuple(kwargs.pop("review", ())),
    )


def test_backward_blocks_drop_narrow_and_nullable_tightening():
    consumer = make_consumer("backward")
    outcomes = [
        evaluate_change(consumer, _policy(), make_change("drop_field")),
        evaluate_change(consumer, _policy(), make_change("narrow_type")),
        evaluate_change(consumer, _policy(), make_change("nullable_to_false")),
    ]
    assert [item.severity for item in outcomes] == ["BLOCK", "BLOCK", "BLOCK"]
    assert outcomes[0].rule_id == "SG-COMP-103"


def test_backward_allows_optional_field_and_relaxed_nullable():
    consumer = make_consumer("backward")
    optional = evaluate_change(
        consumer, _policy(), make_change("add_field", details={"required": False})
    )
    relaxed = evaluate_change(consumer, _policy(), make_change("nullable_to_true"))
    assert optional.severity == "PASS"
    assert relaxed.severity == "PASS"


def test_forward_blocks_required_field_and_drop():
    consumer = make_consumer("forward", read_versions=(), write_versions=("v1",))
    required = evaluate_change(
        consumer, _policy(), make_change("add_field", details={"required": True})
    )
    dropped = evaluate_change(consumer, _policy(), make_change("drop_field"))
    assert required.severity == "BLOCK"
    assert required.rule_id == "SG-COMP-201"
    assert dropped.severity == "BLOCK"
    assert dropped.rule_id == "SG-COMP-203"


def test_forward_allows_optional_field():
    consumer = make_consumer("forward", read_versions=(), write_versions=("v1",))
    outcome = evaluate_change(
        consumer, _policy(), make_change("add_field", details={"required": False})
    )
    assert outcome.severity == "PASS"
    assert outcome.rule_id == "SG-COMP-202"


def test_full_takes_the_stronger_direction():
    consumer = make_consumer("full")
    review = evaluate_change(consumer, _policy(), make_change("default_added"))
    blocked = evaluate_change(consumer, _policy(), make_change("drop_field"))
    assert review.severity == "REVIEW"
    assert review.rule_id == "SG-COMP-211"
    assert blocked.severity == "BLOCK"


def test_none_records_without_blocking_even_when_policy_forbids():
    consumer = make_consumer("none")
    outcome = evaluate_change(
        consumer, _policy(forbidden=("drop_field",)), make_change("drop_field")
    )
    assert outcome.severity == "PASS"
    assert outcome.rule_id == "SG-COMP-400"


def test_allowed_widening_passes_but_undeclared_widening_reviews():
    change = make_change("widen_type", details={"old_type": "int32", "new_type": "int64"})
    allowed = evaluate_change(
        make_consumer("backward", allowed=("int32->int64",)), _policy(), change
    )
    undeclared = evaluate_change(make_consumer("backward"), _policy(), change)
    assert allowed.severity == "PASS"
    assert undeclared.severity == "REVIEW"


def test_partition_change_defaults_to_review_and_can_be_allowed():
    change = make_change("partition_change", path="$partition_spec")
    review = evaluate_change(make_consumer("backward"), _policy(), change)
    allowed = evaluate_change(
        make_consumer("backward", allow_partition=True), _policy(), change
    )
    assert review.severity == "REVIEW"
    assert review.rule_id == "SG-PART-001"
    assert allowed.severity == "PASS"
    assert allowed.rule_id == "SG-PART-002"


def test_policy_forbids_partition_change():
    change = make_change("partition_change", path="$partition_spec")
    outcome = evaluate_change(
        make_consumer("backward", allow_partition=True),
        _policy(forbidden=("partition_change",)),
        change,
    )
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-POL-001"


def test_policy_review_escalates_a_pass():
    outcome = evaluate_change(
        make_consumer("backward"),
        _policy(review=("add_field",)),
        make_change("add_field", details={"required": False}),
    )
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-POL-002"


def test_consumer_override_changes_severity_and_rule():
    outcome = evaluate_change(
        make_consumer("backward", overrides={"drop_field": "REVIEW"}),
        _policy(),
        make_change("drop_field"),
    )
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-POL-003"
    assert outcome.details["base_severity"] == "BLOCK"


def test_case_verdicts_are_independent_and_sorted(events_case, events_result):
    verdicts = evaluate_case(events_case, ())
    assert [item.consumer_id for item in verdicts] == [
        "bi_reader", "etl_writer", "ml_reader"
    ]
    ml_reader = next(item for item in events_result["consumers"]
                     if item["consumer_id"] == "ml_reader")
    assert ml_reader["anchor_version"] == "v2"
    assert ml_reader["decision"] == "BLOCK"
    assert len(ml_reader["findings"]) < len(events_result["consumers"][0]["findings"])
