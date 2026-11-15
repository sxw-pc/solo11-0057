from __future__ import annotations

from schemagate.compatibility import evaluate_case, evaluate_change, window_states
from schemagate.diff_engine import diff_versions
from schemagate.models import Policy
from tests.helpers import field, make_case, make_change, make_consumer, schema


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
    assert ml_reader["anchor_version"] == "v3"
    assert ml_reader["read_anchor_version"] == "v1"
    assert ml_reader["write_anchor_version"] == "v3"
    assert ml_reader["decision"] == "BLOCK"


def test_window_states_are_asymmetric_for_read_and_write():
    case = make_case([schema("v1", [field("a", "string")]),
                      schema("v2", [field("a", "string")]),
                      schema("v3", [field("a", "string")])])
    reader = make_consumer("backward", read_versions=("v1", "v2"))
    read_states, write_states = window_states(case, reader)
    assert read_states == {"v1->v2": "covered", "v2->v3": "exposed"}
    assert set(write_states.values()) == {"excluded"}
    writer = make_consumer("forward", read_versions=(), write_versions=("v2", "v3"))
    read_states, write_states = window_states(case, writer)
    assert set(read_states.values()) == {"excluded"}
    # The cutover inbound edge stays exposed; the internal span is covered.
    assert write_states == {"v1->v2": "exposed", "v2->v3": "covered"}
    greenfield = make_consumer("forward", read_versions=(), write_versions=("v3",))
    _, write_states = window_states(case, greenfield)
    assert write_states == {"v1->v2": "excluded", "v2->v3": "exposed"}


def test_mixed_contract_keeps_write_block_visible_behind_a_read_shadow(events_case, events_result):
    ml_reader = next(item for item in events_result["consumers"]
                     if item["consumer_id"] == "ml_reader")
    windows = {item["pair_id"]: item for item in ml_reader["window_verdicts"]}
    first = windows["v1->v2"]
    second = windows["v2->v3"]
    # Read window starts at v1, write window only reaches the v2->v3 cutover.
    assert first["read_status"] == "exposed"
    assert first["write_status"] == "excluded"
    assert second["read_status"] == "exposed"
    assert second["write_status"] == "exposed"
    # Upstream BLOCK on the read side shadows downstream read findings...
    assert len(second["read_shadowed_change_ids"]) == 8
    assert second["write_shadowed_change_ids"] == []
    # ...but never the write side; a write BLOCK cannot be eaten by a read PASS.
    v23 = [f for f in ml_reader["findings"] if f["pair_id"] == "v2->v3"]
    assert all(f["read_shadow"] for f in v23)
    assert not any(f["write_shadow"] for f in v23)
    assert not any(f["downgrade_shadow"] for f in v23)
    assert ml_reader["shadowed_finding_count"] == 0
    blocked = next(f for f in v23 if f["change_id"].endswith("payload.device:drop_field"))
    assert blocked["severity"] == "BLOCK"
    assert blocked["read_status"] == "exposed"
    assert blocked["write_status"] == "exposed"


def test_downgrade_shadow_only_marks_findings_shadowed_on_every_applicable_side(
    events_result,
):
    bi = next(c for c in events_result["consumers"] if c["consumer_id"] == "bi_reader")
    etl = next(c for c in events_result["consumers"] if c["consumer_id"] == "etl_writer")
    # Single-direction consumers: upstream BLOCK on the same side moots the pair.
    assert bi["shadowed_finding_count"] == 8
    assert etl["shadowed_finding_count"] == 8
    shadowed = events_result["downgrade_shadow_items"]
    assert {(item["consumer_id"], item["pair_id"]) for item in shadowed} == {
        ("bi_reader", "v2->v3"),
        ("etl_writer", "v2->v3"),
    }
    assert all(item["downgrade_shadow"] for item in shadowed)
    # Shadowed findings are removed from the blocking/review buckets.
    blocked_ids = {(item["consumer_id"], item["change_id"])
                   for item in events_result["blocked_items"]}
    assert not any(
        item["pair_id"] == "v2->v3" and item["consumer_id"] == "bi_reader"
        for item in events_result["blocked_items"]
    )
    assert ("bi_reader", "demo_lake_events:v1->v2:debug_flag:drop_field") in blocked_ids


def test_finance_reader_window_is_monotone(orders_result):
    finance = orders_result["consumers"][0]
    assert finance["consumer_id"] == "finance_reader"
    assert finance["shadowed_finding_count"] == 0
    window = finance["window_verdicts"][0]
    assert window["pair_id"] == "v1->v2"
    assert window["read_status"] == "exposed"
    assert window["write_status"] == "excluded"
    assert window["read_verdict"] == "REVIEW"
    assert window["write_verdict"] == "N/A"
    assert orders_result["downgrade_shadow_items"] == []


def test_shadowing_respects_window_containment_not_severity_max():
    # Three versions, a read-v1 backward consumer; the first pair has no BLOCK,
    # so the downstream pair must remain fully visible even though it is harsher.
    v1 = schema("v1", [field("id", "string"), field("note", "string")])
    v2 = schema("v2", [field("id", "string"), field("note", "string"),
                       field("tag", "string")])
    v3 = schema("v3", [field("id", "string"), field("tag", "string")])
    case = make_case([v1, v2, v3], consumers=(make_consumer("backward"),))
    changes = (
        *diff_versions(case, v1, v2),
        *diff_versions(case, v2, v3),
    )
    verdict = evaluate_case(case, changes)[0]
    windows = {item.pair_id: item for item in verdict.window_verdicts}
    assert windows["v1->v2"].read_verdict == "PASS"
    assert windows["v2->v3"].read_verdict == "BLOCK"
    assert windows["v2->v3"].read_shadowed_change_ids == ()
    assert verdict.decision == "BLOCK"
    assert verdict.shadowed_finding_count == 0
