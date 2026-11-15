from __future__ import annotations


def test_events_plan_orders_readers_before_writers(events_result):
    steps = events_result["migration_steps"]
    assert [item["number"] for item in steps] == [1, 2, 3, 4, 5]
    assert steps[0]["action"].startswith("Publish declared schema v2")
    assert steps[0]["affected_consumers"] == ["bi_reader"]
    assert steps[1]["action"].startswith("Enable writer cutover to v2")
    assert steps[1]["affected_consumers"] == ["etl_writer"]
    assert steps[2]["target_version"] == "v3"
    assert steps[2]["affected_consumers"] == ["bi_reader", "ml_reader"]
    assert steps[3]["action"].startswith("Enable writer cutover to v3")
    assert steps[3]["affected_consumers"] == ["etl_writer"]
    assert steps[4]["action"].startswith("Retire compatibility")


def test_events_blocked_steps_expose_reasons(events_result):
    blocked = [step for step in events_result["migration_steps"] if step["status"] == "blocked"]
    assert blocked
    assert all(step["blocking_reasons"] for step in blocked)
    assert any("SG-COMP" in reason for step in blocked for reason in step["blocking_reasons"])
    assert events_result["migration_steps"][-1]["reversible"] is False


def test_events_rollback_hints_keep_consumer_touched_fields(events_result):
    hints = events_result["rollback_hints"]
    pair_hint = next(item for item in hints if item["version_pair"] == "v1->v2")
    assert pair_hint["rollback_to"] == "v1"
    # Fields the v1 readers/writer actually touch, plus both paths of the
    # field_id-9 identity (legacy_code -> source_code rename).
    assert "debug_flag" in pair_hint["retain_fields"]
    assert "legacy_code" in pair_hint["retain_fields"]
    assert "source_code" in pair_hint["retain_fields"]
    assert "payload.device" in pair_hint["retain_fields"]
    assert "event_id" in pair_hint["retain_fields"]
    assert "bi_reader" in pair_hint["consumers_to_rollback"]
    assert "etl_writer" in pair_hint["consumers_to_rollback"]
    details = pair_hint["details"]
    assert details["rollback_point"] == "v1"
    assert details["consumer_versions"]["bi_reader"]["read_versions"] == ["v1"]
    assert details["consumer_versions"]["etl_writer"]["write_versions"] == ["v1"]
    # No identity disappears at v1 and flows back later, so no reuse markers.
    assert details["reuse_across_rollback"] == []
    second_hint = next(item for item in hints if item["version_pair"] == "v2->v3")
    assert second_hint["details"]["rollback_point"] == "v2"
    assert "payload.device" in second_hint["retain_fields"]
    assert "payload.channel" in second_hint["retain_fields"]
    assert second_hint["details"]["reuse_across_rollback"] == []
    assert hints[-1]["version_pair"] == "all"
    assert hints[-1]["details"]["reuse_across_rollback"] == []


def test_rollback_hint_retain_fields_are_stably_sorted(events_result):
    for hint in events_result["rollback_hints"]:
        assert hint["retain_fields"] == sorted(hint["retain_fields"])
        assert len(hint["retain_fields"]) == len(set(hint["retain_fields"]))


def test_rollback_hints_are_deterministic_across_repeated_runs(events_case):
    from schemagate.runner import build_result

    first = build_result(events_case)["rollback_hints"]
    second = build_result(events_case)["rollback_hints"]
    assert first == second


def test_reflow_after_drop_with_same_field_id_is_marked_reuse():
    """A field dropped at the rollback point and re-added later with the same
    field_id must be retained and annotated reuse_across_rollback."""
    from schemagate.compatibility import evaluate_case
    from schemagate.diff_engine import diff_versions
    from schemagate.planner import build_plan
    from tests.helpers import field, make_case, make_consumer, schema

    v1 = schema("v1", [
        field("id", "string", nullable=False, field_id=1),
        field("token", "string", field_id=7),
    ])
    v2 = schema("v2", [
        field("id", "string", nullable=False, field_id=1),
    ])
    v3 = schema("v3", [
        field("id", "string", nullable=False, field_id=1),
        # Reflow under the same name and field_id; required so the v2-anchored
        # reader has a non-PASS finding and appears on the rollback hint.
        field("token", "string", nullable=False, field_id=7),
    ])
    consumer = make_consumer(
        "backward", consumer_id="reader", read_versions=("v1", "v2")
    )
    case = make_case([v1, v2, v3], consumers=(consumer,))
    changes = diff_versions(case, v1, v2) + diff_versions(case, v2, v3)
    verdicts = evaluate_case(case, changes)
    _, hints = build_plan(case, changes, verdicts)

    first = next(item for item in hints if item.version_pair == "v1->v2")
    assert first.retain_fields == ()  # token already gone at the v1 rollback point
    assert first.details["reuse_across_rollback"] == []

    second = next(item for item in hints if item.version_pair == "v2->v3")
    assert second.retain_fields == ("id", "token")
    reuse = second.details["reuse_across_rollback"]
    assert len(reuse) == 1
    entry = reuse[0]
    assert entry["kind"] == "reuse_across_rollback"
    assert entry["retained_paths"] == ["token"]
    assert entry["field_id"] == 7
    assert entry["consumer_ids"] == ["reader"]
    assert entry["source_pairs"] == ["v1->v2", "v2->v3"]

    final_hint = next(item for item in hints if item.version_pair == "all")
    assert "token" in final_hint.retain_fields
    merged = final_hint.details["reuse_across_rollback"]
    assert merged[0]["retained_paths"] == ["token"]
    assert merged[0]["source_pairs"] == ["v1->v2", "v2->v3"]


def test_renamed_struct_child_declared_by_reader_keeps_both_paths():
    """A struct child rename (field_id preserved, path reorganized) keeps both
    child paths for a reader that declared the old version, without a reuse
    marker because the identity never disappears at the rollback point."""
    from schemagate.compatibility import (
        build_field_identity,
        evaluate_case,
        rollback_retain_plan,
    )
    from schemagate.diff_engine import diff_versions
    from schemagate.planner import build_plan
    from tests.helpers import field, make_case, make_consumer, schema

    v1 = schema("v1", [
        field("id", "string", nullable=False, field_id=1),
        field("envelope", "struct", field_id=5, fields=[
            field("device", "string", field_id=62),
        ]),
    ])
    v2 = schema("v2", [
        field("id", "string", nullable=False, field_id=1),
        field("envelope", "struct", field_id=5, fields=[
            field("channel", "string", field_id=62),
        ]),
    ])
    consumer = make_consumer(
        "backward", consumer_id="reader", read_versions=("v1",)
    )
    case = make_case([v1, v2], consumers=(consumer,))
    changes = diff_versions(case, v1, v2)
    rename = next(item for item in changes if item.change_type == "rename_field")
    assert (rename.details["old_path"], rename.details["new_path"]) == (
        "envelope.device", "envelope.channel",
    )
    verdicts = evaluate_case(case, changes)
    _, hints = build_plan(case, changes, verdicts)
    hint = hints[0]
    assert hint.version_pair == "v1->v2"
    assert "envelope.device" in hint.retain_fields
    assert "envelope.channel" in hint.retain_fields
    assert "envelope" in hint.retain_fields
    assert hint.details["reuse_across_rollback"] == []

    # Helper-level check: identity normalization joins the reorganized child
    # paths and locates them from the rollback point.
    identity = build_field_identity(case, changes)
    assert identity.equivalence_paths("v1", "envelope.device") == (
        "envelope.channel", "envelope.device",
    )
    plan = rollback_retain_plan(case, "v1", ("reader",), identity)
    assert "envelope.device" in plan["retain_fields"]
    assert "envelope.channel" in plan["retain_fields"]
    assert plan["reuse_across_rollback"] == []
    assert plan["consumer_versions"]["reader"]["read_versions"] == ["v1"]


def test_orders_plan_is_review_not_blocked(orders_result):
    assert orders_result["decision"] == "REVIEW"
    assert [item["status"] for item in orders_result["migration_steps"]] == ["review", "review"]
    hint = orders_result["rollback_hints"][0]
    assert hint["rollback_to"] == "v1"
    assert hint["consumers_to_rollback"] == ["finance_reader"]


def test_plan_actions_contain_no_executable_ddl(events_result):
    forbidden = ("ALTER TABLE", "DROP TABLE", "CREATE TABLE", "INSERT INTO")
    for step in events_result["migration_steps"]:
        assert not any(word in step["action"].upper() for word in forbidden)
