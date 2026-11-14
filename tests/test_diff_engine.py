from __future__ import annotations

from tests.helpers import diff_docs, field


def _diff(old_fields, new_fields):
    return diff_docs(
        {"table": "demo.unit", "version": "v1", "fields": old_fields},
        {"table": "demo.unit", "version": "v2", "fields": new_fields},
    )


def _typed(changes, change_type):
    return [change for change in changes if change.change_type == change_type]


def test_add_field_records_required_and_evidence():
    changes = _diff(
        [field("id", "string")],
        [field("id", "string"), field("required_value", "int64", nullable=False)],
    )
    added = _typed(changes, "add_field")
    assert len(added) == 1
    assert added[0].field_path == "required_value"
    assert added[0].details["required"] is True
    assert added[0].evidence_path.endswith("versions.v2.fields.required_value.presence")
    assert "v1->v2" in added[0].change_id


def test_drop_field():
    changes = _diff(
        [field("id", "string"), field("legacy", "string")],
        [field("id", "string")],
    )
    dropped = _typed(changes, "drop_field")
    assert [item.field_path for item in dropped] == ["legacy"]
    assert dropped[0].evidence_path.endswith("versions.v1.fields.legacy.presence")


def test_rename_by_field_id_is_explicit():
    changes = _diff(
        [field("old_name", "string", field_id=7)],
        [field("new_name", "string", field_id=7)],
    )
    assert [item.field_path for item in changes] == ["new_name"]
    assert changes[0].change_type == "rename_field"
    assert changes[0].details["old_path"] == "old_name"


def test_rename_by_alias_declaration():
    changes = _diff(
        [field("old_name", "string", aliases=["new_name"])],
        [field("new_name", "string")],
    )
    assert len(_typed(changes, "rename_field")) == 1
    assert not _typed(changes, "add_field")
    assert not _typed(changes, "drop_field")


def test_delete_plus_add_is_not_guessed_as_rename():
    changes = _diff(
        [field("old_name", "string")],
        [field("new_name", "string")],
    )
    assert _typed(changes, "drop_field")
    assert _typed(changes, "add_field")
    assert not _typed(changes, "rename_field")


def test_type_widening_and_narrowing():
    widened = _diff([field("value", "int32")], [field("value", "int64")])
    narrowed = _diff([field("value", "int64")], [field("value", "int32")])
    assert _typed(widened, "widen_type")[0].details["direction"] == "widen"
    assert _typed(narrowed, "narrow_type")[0].details["direction"] == "narrow"


def test_nullable_changes_both_directions():
    relaxed = _diff([field("value", "string", nullable=False)], [field("value", "string")])
    tightened = _diff([field("value", "string")], [field("value", "string", nullable=False)])
    assert len(_typed(relaxed, "nullable_to_true")) == 1
    assert len(_typed(tightened, "nullable_to_false")) == 1


def test_default_add_remove_change():
    added = _diff([field("value", "int64")], [field("value", "int64", default=3)])
    removed = _diff([field("value", "int64", default=3)], [field("value", "int64")])
    changed = _diff(
        [field("value", "int64", default=3)],
        [field("value", "int64", default=4)],
    )
    assert _typed(added, "default_added")[0].details["new_default"] == 3
    assert _typed(removed, "default_removed")
    assert _typed(changed, "default_changed")


def test_field_order_change():
    changes = _diff(
        [field("alpha", "string"), field("beta", "string")],
        [field("beta", "string"), field("alpha", "string")],
    )
    order = _typed(changes, "field_order_change")
    assert len(order) == 1
    assert order[0].details["old_order"] == ["alpha", "beta"]
    assert order[0].details["new_order"] == ["beta", "alpha"]


def test_nested_struct_change_keeps_nested_paths():
    changes = _diff(
        [field("payload", "struct", fields=[field("action", "string")])],
        [field("payload", "struct", fields=[
            field("action", "string"),
            field("channel", "string"),
        ])],
    )
    assert _typed(changes, "add_field")[0].field_path == "payload.channel"
    aggregate = _typed(changes, "struct_field_change")
    assert aggregate[0].field_path == "payload"
    assert aggregate[0].details["changed_paths"] == ["payload.channel"]


def test_nested_struct_drop_and_field_order():
    changes = _diff(
        [field("payload", "struct", fields=[field("a", "string"), field("b", "string")])],
        [field("payload", "struct", fields=[field("b", "string")])],
    )
    assert _typed(changes, "drop_field")[0].field_path == "payload.a"
    assert _typed(changes, "struct_field_change")


def test_list_and_map_element_changes():
    listed = _diff([field("tags", "list<string>")], [field("tags", "list<int64>")])
    mapped = _diff(
        [field("lookup", "map<string,int32>")],
        [field("lookup", "map<string,int64>")],
    )
    assert _typed(listed, "element_type_change")[0].details["collection"] == "list"
    assert _typed(mapped, "element_type_change")[0].details["collection"] == "map"


def test_decimal_change_details():
    changes = _diff(
        [field("amount", "decimal(10,2)")],
        [field("amount", "decimal(12,2)")],
    )
    change = _typed(changes, "decimal_change")[0]
    assert change.details["direction"] == "widen"
    assert "decimal(10,2)" in change.summary
    assert "decimal(12,2)" in change.summary


def test_timestamp_unit_change():
    changes = _diff(
        [field("event_time", "timestamp_ms")],
        [field("event_time", "timestamp_us")],
    )
    assert _typed(changes, "timestamp_unit_change")[0].details["new_type"] == "timestamp_us"


def test_struct_to_scalar_is_incompatible_type_change():
    changes = _diff(
        [field("payload", "struct", fields=[field("a", "string")])],
        [field("payload", "string")],
    )
    assert _typed(changes, "type_change")
    assert not _typed(changes, "struct_field_change")
