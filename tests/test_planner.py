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


def test_events_rollback_hints_keep_old_fields(events_result):
    hints = events_result["rollback_hints"]
    pair_hint = next(item for item in hints if item["version_pair"] == "v1->v2")
    assert pair_hint["rollback_to"] == "v1"
    assert "debug_flag" in pair_hint["retain_fields"]
    assert "legacy_code" in pair_hint["retain_fields"]
    assert "source_code" in pair_hint["retain_fields"]
    assert "bi_reader" in pair_hint["consumers_to_rollback"]
    assert hints[-1]["version_pair"] == "all"


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
