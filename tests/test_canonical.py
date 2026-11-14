from __future__ import annotations

import pytest
import yaml

from schemagate.canonical import canonical_json, schema_fingerprint
from schemagate.errors import SchemaGateError
from schemagate.loader import CaseLoader
from schemagate.schema_parser import parse_schema_document
from schemagate.type_rules import decimal_direction, parse_type
from tests.helpers import dump_yaml, field, schema


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BOOL", "boolean"),
        ("BigInt", "int64"),
        ("double", "float64"),
        ("timestamp_millis", "timestamp_ms"),
        ("Decimal(12, 2)", "decimal(12,2)"),
    ],
)
def test_type_name_normalization(raw, expected):
    assert parse_type(raw).kind in {expected, "decimal"}
    if expected != "decimal(12,2)":
        assert parse_type(raw).kind == expected


def test_decimal_and_collection_normalization():
    decimal = parse_type("decimal( 18 , 4 )")
    assert (decimal.kind, decimal.precision, decimal.scale) == ("decimal", 18, 4)
    assert parse_type("list<INT32>").kind == "list"
    assert parse_type("list<INT32>").element.kind == "int32"
    mapping = parse_type("map<string, list<int64>>")
    assert mapping.kind == "map"
    assert mapping.value.kind == "list"


def test_invalid_decimal_is_rejected():
    with pytest.raises(SchemaGateError) as caught:
        parse_type("decimal(2,5)")
    assert caught.value.code == "SG-LOAD-003"


def test_decimal_direction():
    assert decimal_direction(parse_type("decimal(10,2)"), parse_type("decimal(12,2)")) == "widen"
    assert decimal_direction(parse_type("decimal(12,2)"), parse_type("decimal(10,2)")) == "narrow"


def test_canonical_json_sorts_mapping_keys():
    assert canonical_json({"b": 1, "a": {"d": 4, "c": 3}}) == (
        '{"a":{"c":3,"d":4},"b":1}'
    )


def test_aliases_are_sorted_and_field_index_is_flat():
    parsed = schema("v1", [
        field("root", "struct", fields=[
            field("child", "string", aliases=["z_alias", "a_alias"]),
        ]),
    ])
    child = parsed.field_index()["root.child"]
    assert child.aliases == ("a_alias", "z_alias")
    assert set(parsed.field_index()) == {"root", "root.child"}


def test_schema_fingerprint_is_stable_and_content_sensitive():
    first = schema("v1", [field("id", "string"), field("score", "int32")])
    second = schema("v1", [field("score", "int32"), field("id", "string")])
    changed = schema("v1", [field("id", "string"), field("score", "int64")])
    assert schema_fingerprint(first) == schema_fingerprint(second)
    assert schema_fingerprint(first) != schema_fingerprint(changed)


def test_input_fingerprint_is_stable_for_reformatted_fixture(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    before = CaseLoader(fixtures).load_case("lake_orders")
    schema_path = case_dir / "schemas" / "v1.yaml"
    document = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    dump_yaml(schema_path, document)
    after = CaseLoader(fixtures).load_case("lake_orders")
    assert before.input_fingerprint == after.input_fingerprint


def test_input_fingerprint_changes_with_contract(copied_case):
    fixtures, case_dir = copied_case("lake_orders")
    before = CaseLoader(fixtures).load_case("lake_orders")
    consumer_path = case_dir / "consumers" / "finance_reader.yaml"
    document = yaml.safe_load(consumer_path.read_text(encoding="utf-8"))
    document["mode"] = "full"
    dump_yaml(consumer_path, document)
    after = CaseLoader(fixtures).load_case("lake_orders")
    assert before.input_fingerprint != after.input_fingerprint


def test_default_values_are_stored_as_json():
    parsed = parse_schema_document({
        "table": "demo.unit",
        "version": "v1",
        "fields": [field("payload", "string", default={"z": 1, "a": [True, None]})],
    })
    assert parsed.fields[0].default == {"a": [True, None], "z": 1}
