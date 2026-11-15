from __future__ import annotations

from schemagate.compatibility import (
    build_field_identity,
    consumer_field_paths,
    evaluate_case,
    evaluate_change,
    rollback_retain_plan,
)
from schemagate.models import Policy
from tests.helpers import (
    field,
    make_case,
    make_change,
    make_consumer,
    schema,
)


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


def _reflow_case(*, use_field_id: bool, alias: str | None):
    aliases = [alias] if alias else []
    v1 = schema("v1", [
        field("id", "string", nullable=False, field_id=1),
        field("code", "string", field_id=9, aliases=aliases),
    ])
    v2 = schema("v2", [
        field("id", "string", nullable=False, field_id=1),
    ])
    v3 = schema("v3", [
        field("id", "string", nullable=False, field_id=1),
        field("source_code", "string", nullable=False,
              field_id=9 if use_field_id else 99,
              aliases=aliases),
    ])
    consumer = make_consumer(
        "backward", consumer_id="reader", read_versions=("v1", "v2")
    )
    case = make_case([v1, v2, v3], consumers=(consumer,))
    return case, consumer


def test_field_identity_links_alias_reflow_across_missing_version():
    from schemagate.diff_engine import diff_versions

    case, _consumer = _reflow_case(use_field_id=False, alias="old_code")
    changes = diff_versions(case, case.versions[0], case.versions[1]) + diff_versions(
        case, case.versions[1], case.versions[2]
    )
    identity = build_field_identity(case, changes)
    # Different field_id, but the shared alias carries the identity across v2.
    assert identity.equivalence_paths("v1", "code") == ("code", "source_code")
    plan = rollback_retain_plan(case, "v2", ("reader",), identity)
    reuse = plan["reuse_across_rollback"]
    assert len(reuse) == 1
    assert reuse[0]["retained_paths"] == ["code", "source_code"]
    assert reuse[0]["source_pairs"] == ["v1->v2", "v2->v3"]
    assert reuse[0]["consumer_ids"] == ["reader"]
    assert set(plan["retain_fields"]) == {"code", "id", "source_code"}


def test_same_name_without_id_or_alias_is_not_treated_as_reflow():
    from schemagate.diff_engine import diff_versions

    case, consumer = _reflow_case(use_field_id=False, alias=None)
    # Rename the v3 field to the same bare name as v1 but with no identity
    # evidence: the helper must not bridge the v2 gap on name similarity alone.
    v3_fields = [
        field("id", "string", nullable=False, field_id=1),
        field("code", "string", nullable=False, field_id=99),
    ]
    case = make_case(
        [case.versions[0], case.versions[1], schema("v3", v3_fields)],
        consumers=(consumer,),
    )
    changes = diff_versions(case, case.versions[0], case.versions[1]) + diff_versions(
        case, case.versions[1], case.versions[2]
    )
    identity = build_field_identity(case, changes)
    assert identity.equivalence_paths("v3", "code") == ("code",)
    plan = rollback_retain_plan(case, "v2", ("reader",), identity)
    assert plan["reuse_across_rollback"] == []


def test_consumer_field_paths_only_uses_versions_at_or_after_rollback_point():
    case, consumer = _reflow_case(use_field_id=True, alias="old_code")
    at_v1 = consumer_field_paths(case, consumer, "v1")
    at_v2 = consumer_field_paths(case, consumer, "v2")
    # Raw declared paths only; equivalence expansion happens in the plan.
    assert "code" in at_v1 and "source_code" not in at_v1
    # v2 itself has no code; the consumer's v1 declaration is below the floor.
    assert at_v2 == ("id",)
    # Unknown rollback version degrades to an empty set rather than raising.
    assert consumer_field_paths(case, consumer, "v9") == ()
