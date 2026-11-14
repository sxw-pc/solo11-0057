"""Stable identifiers and readable evidence paths."""

from __future__ import annotations

import re


def table_id(table: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", table).strip("_")
    return cleaned or "table"


def pair_id(from_version: str, to_version: str) -> str:
    return f"{from_version}->{to_version}"


def change_id(table: str, pair: str, field_path: str, change_type: str) -> str:
    path = field_path or "$fields"
    return f"{table_id(table)}:{pair}:{path}:{change_type}"


def field_evidence(table: str, version: str, field_path: str, aspect: str) -> str:
    path = field_path or "$fields"
    return f"tables.{table_id(table)}.versions.{version}.fields.{path}.{aspect}"


def partition_evidence(table: str, version: str) -> str:
    return f"tables.{table_id(table)}.versions.{version}.partition_spec"
