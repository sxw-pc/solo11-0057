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


def _partition_with_blocked(blocked=("region",), *, version="v2"):
    return make_change(
        "partition_change",
        path="$partition_spec",
        details={
            "blocked_keys": list(blocked),
            "consistency_version": version,
            "consistency_evidence": [
                {
                    "column": column,
                    "status": "blocked",
                    "reason": "missing_column",
                    "exists": False,
                    "transform": "identity",
                }
                for column in blocked
            ],
        },
    )


def test_inconsistent_partition_defaults_to_review_with_evidence():
    change = _partition_with_blocked()
    outcome = evaluate_change(make_consumer("backward"), _policy(), change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-PART-003"
    assert outcome.details["blocked_keys"] == ["region"]
    assert outcome.details["consistency_evidence"][0]["column"] == "region"
    assert outcome.details["base_severity"] == "REVIEW"


def test_inconsistent_partition_cannot_be_cleared_by_allow_or_pass_override():
    change = _partition_with_blocked()
    allowed = evaluate_change(
        make_consumer("backward", allow_partition=True), _policy(), change
    )
    override_pass = evaluate_change(
        make_consumer("backward", overrides={"partition_change": "PASS"}),
        _policy(),
        change,
    )
    assert allowed.severity == "REVIEW"
    assert allowed.rule_id == "SG-PART-003"
    assert override_pass.severity == "REVIEW"
    assert override_pass.rule_id == "SG-POL-003"


def test_inconsistent_partition_override_can_raise_to_block():
    change = _partition_with_blocked()
    outcome = evaluate_change(
        make_consumer("backward", overrides={"partition_change": "BLOCK"}),
        _policy(),
        change,
    )
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-POL-003"
    assert outcome.details["blocked_keys"] == ["region"]


def test_partition_change_forbidden_by_policy_blocks_with_evidence():
    change = _partition_with_blocked()
    outcome = evaluate_change(
        make_consumer("backward", allow_partition=True),
        _policy(forbidden=("partition_change",)),
        change,
    )
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-POL-001"
    assert outcome.details["blocked_keys"] == ["region"]


def test_consistent_partition_change_keeps_existing_precedence():
    consistent = make_change("partition_change", path="$partition_spec")
    review = evaluate_change(make_consumer("backward"), _policy(), consistent)
    allowed = evaluate_change(
        make_consumer("backward", allow_partition=True), _policy(), consistent
    )
    override = evaluate_change(
        make_consumer("backward", overrides={"partition_change": "REVIEW"}),
        _policy(),
        consistent,
    )
    assert (review.severity, review.rule_id) == ("REVIEW", "SG-PART-001")
    assert (allowed.severity, allowed.rule_id) == ("PASS", "SG-PART-002")
    assert (override.severity, override.rule_id) == ("REVIEW", "SG-POL-003")


def test_non_partition_changes_ignore_partition_evidence():
    change = make_change("drop_field", details={"blocked_keys": ["region"]})
    outcome = evaluate_change(make_consumer("backward"), _policy(), change)
    assert outcome.severity == "BLOCK"
    assert outcome.rule_id == "SG-COMP-103"


def _partition_case(new_keys):
    from schemagate.contracts_parser import parse_partition_spec
    from schemagate.diff_engine import diff_versions
    from schemagate.schema_parser import parse_schema_document
    from tests.helpers import field, make_case

    old = parse_schema_document(
        {"table": "demo.unit", "version": "v1", "fields": [field("id", "string")]}
    )
    new = parse_schema_document(
        {"table": "demo.unit", "version": "v2",
         "fields": [field("id", "string"), field("region", "string")]}
    )
    old_spec = parse_partition_spec(
        {"table": "demo.unit", "version": "v1", "partition_keys": [],
         "sort_columns": [], "file_format": "parquet"},
        expected_version="v1", expected_table="demo.unit",
    )
    new_spec = parse_partition_spec(
        {"table": "demo.unit", "version": "v2", "partition_keys": new_keys,
         "sort_columns": [], "file_format": "parquet"},
        expected_version="v2", expected_table="demo.unit",
    )
    case = make_case(
        [old, new],
        partitions={"v1": old_spec, "v2": new_spec},
        consumers=(make_consumer("backward", allow_partition=True),),
    )
    change = next(
        item for item in diff_versions(case, old, new)
        if item.change_type == "partition_change"
    )
    return case, change


def test_end_to_end_inconsistent_partition_cannot_be_allowed():
    case, change = _partition_case([{"column": "gone", "transform": "identity"}])
    assert change.details["blocked_keys"] == ["gone"]
    outcome = evaluate_change(case.consumers[0], case.policy, change)
    assert outcome.severity == "REVIEW"
    assert outcome.rule_id == "SG-PART-003"
    assert outcome.details["blocked_keys"] == ["gone"]


def test_end_to_end_compatible_added_key_still_can_be_allowed():
    case, change = _partition_case(
        [{"column": "region", "transform": "bucket", "bucket_count": 8}]
    )
    assert "blocked_keys" not in change.details
    outcome = evaluate_change(case.consumers[0], case.policy, change)
    assert (outcome.severity, outcome.rule_id) == ("PASS", "SG-PART-002")
