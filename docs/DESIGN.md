# Design and data flow

## 1. Goal and boundaries

SchemaGate answers one local question:

> Given a declared sequence of table schema versions, a set of consumer
> contracts, partition specifications, and a case policy, is the next evolution
> admissible?

It is an admission gate, not a migration runtime. It reads declarative YAML,
computes deterministic evidence, and writes reports plus a local ledger. It
never reads table data, never calls a catalog, and never executes DDL.

The primary interface is an HTTP service bound to loopback. The CLI exists only
to start that service.

## 2. System context

```text
local case directory
  manifest.yaml
  schemas/*.yaml
  consumers/*.yaml
  partitions/*.yaml
  policies/*.yaml
          |
          v
   +--------------+     +-------------+     +--------------+
   | CaseLoader   | --> | Diff engine | --> | Compatibility|
   +--------------+     +-------------+     +--------------+
          |                    |                    |
          |                    v                    v
          |             partition diff        findings/verdicts
          |                                         |
          +------------------+----------------------+
                             v
                      +--------------+
                      |    Planner   |
                      +--------------+
                             |
                             v
             +-------------------------------+
             | Result builder + renderers    |
             | JSON | Markdown | HTML        |
             +-------------------------------+
                    |                    |
                    v                    v
             local report files    SQLite ledger
                    |
                    v
             loopback HTTP API / index page
```

There are no external services in this diagram and no outbound dependencies at
runtime.

## 3. Module responsibilities

| Module | Responsibility |
| --- | --- |
| `config.py` | Version constants, vocabularies, default paths, loopback/port validation. |
| `errors.py` | One exception with a stable code, message, and detail mapping. |
| `models.py` | Frozen dataclasses for types, fields, versions, partitions, contracts, changes, findings, verdicts, plans, outcomes. |
| `type_rules.py` | Type alias normalization, decimal and generic parsing, signatures, widening and decimal direction. |
| `schema_parser.py` | Schema document validation, field trees, defaults, constraints, aliases, duplicate checks. |
| `contracts_parser.py` | Policy, consumer, and partition document validation. |
| `loader.py` | Fixture discovery, duplicate-key YAML loading, safe case-relative file access, case assembly, fingerprints. |
| `canonical.py` | JSON normalization, canonical serialization, schema and input fingerprints. |
| `diff_engine.py` | Pairwise structured schema diff with explicit rename evidence. |
| `partition_rules.py` | Declarative partition-spec diff. |
| `compatibility.py` | Consumer/mode/policy evaluation and aggregation inputs. |
| `planner.py` | Advisory migration ordering and rollback hints. |
| `runner.py` | Result assembly, deterministic run ID, report writes, service facade. |
| `ledger.py` | SQLite schema, idempotent run registration, parameterized queries. |
| `report_json.py`, `report_markdown.py`, `report_html.py` | Deterministic renderers. |
| `api.py`, `app.py`, `__main__.py` | Loopback HTTP surface, index page, startup validation. |

## 4. Case loading

### 4.1 Discovery

`CaseLoader` resolves the fixtures root once. It scans only direct child
directories containing `manifest.yaml`, sorted by name. The manifest
`case_id` must match the directory name and must satisfy a lowercase identifier
pattern. Duplicate case IDs and unknown cases have distinct stable errors.

Only registered case IDs are accepted by `load_case`. API requests cannot name
an arbitrary path or case directory.

### 4.2 Safe reads

Every referenced case file goes through `_safe_file`:

1. Must be a non-empty relative string.
2. Must not be absolute.
3. Is resolved against the resolved case directory.
4. Must equal the case directory or be beneath it.

Paths that escape, including `..` traversal and absolute Windows or POSIX
paths, fail with `SG-LOAD-013`. Symlink resolution is part of `Path.resolve()`,
so the final target is constrained as well.

### 4.3 YAML

A custom `SafeLoader` rejects duplicate mapping keys. The timestamp implicit
resolver is removed so ISO-looking defaults remain strings and JSON-safe. All
other YAML constructs are constrained by the safe loader. A YAML error becomes
`SG-LOAD-001`.

### 4.4 Validation

The loader validates manifest shape, schema versions, partition specs,
consumers, and policy. Individual parsers enforce:

- Required fields and allowed attribute sets.
- Identifiers and version labels.
- Field path and `field_id` uniqueness across nested fields.
- Type syntax and decimal precision/scale bounds.
- JSON-safe default and constraint values.
- Consumer version references against the declared versions.
- Mode, change-type, severity, and transform vocabularies.
- Non-empty consumer declarations and non-empty case consumer set.

The loader sorts consumers by ID and normalizes policy lists, aliases, widening
declarations, severity overrides, and consumer version lists where order is not
semantic.

## 5. Canonicalization and fingerprints

### 5.1 JSON normalization

`normalize_json_value` accepts JSON scalars, lists, and string-keyed mappings.
Floats must be finite. Mapping keys are sorted; list order is preserved.
Canonical JSON uses UTF-8, `sort_keys=True`, compact separators for hashing, and
no NaN/Infinity.

### 5.2 Input fingerprint

The semantic input payload contains:

- Input schema version and case metadata.
- Table description.
- Schema versions in manifest order.
- Every field's path, type signature, nullability, default, default presence,
  field ID, sorted aliases, doc, constraints, children, and declared order.
- Partition keys in declared order with transform parameters, sort columns,
  and file format.
- Consumers sorted by ID with sorted read/write versions, mode, widening
  pairs, partition permission, and sorted severity overrides.
- Policy ID, default mode, and sorted forbidden/review lists.

The SHA-256 of canonical JSON is the input fingerprint:

```text
sha256:<64 lowercase hex characters>
```

Declared field, partition, and version order are deliberately preserved because
order changes are meaningful. Unordered collections are sorted. This makes YAML
reformatting and key-order changes fingerprint-stable while preserving semantic
order changes.

### 5.3 Schema fingerprints

A schema fingerprint sorts fields by path and omits declaration order. This
separates "the same field set reordered" from "a different field definition."
The diff engine detects the reordering separately.

### 5.4 Run identity

```text
run_id = "run-" + first 24 hex chars of
         SHA-256(canonical JSON({
           case_id, input_fingerprint, tool_version,
           rules_version, report_schema_version
         }))
```

No clock, randomness, environment, or filesystem timestamp enters the ID. The
run ID is derived from the full input fingerprint only; hierarchical
sub-fingerprints (below) never participate in run identity, so adding the
breakdown does not change idempotency.

### 5.5 Hierarchical fingerprint breakdown

`fingerprint_breakdown(case)` gives reviewers a layered impact view instead of
one opaque hash. Every sub-fingerprint reuses the same canonical builders as
the full input payload; the breakdown explains the full hash rather than
re-interpreting the case.

- **Domain fingerprints** under `domains`: one SHA-256 each for the four
  declaration domains `schema`, `partition`, `consumer`, and `policy`.
- **Pair fingerprints** under `version_pairs`: one per adjacent version pair,
  hashing both sides of the pair (each schema with declared field order) and
  the partition spec declared on each side. A schema or partition change moves
  exactly the pairs that touch the changed version.
- **Consumer fingerprints** under `consumers`: one per consumer, hashing its
  full declaration — mode, sorted read/write versions, allowed type widening,
  partition-change permission, and severity overrides. Each entry also carries
  `anchor_version` and `affected_pair_ids`.
- `affected_pair_ids` reuses the anchor/`declared_versions` semantics of
  `compatibility.affected_changes`: a pair affects a consumer when its
  `from_version` position is at or after the consumer's anchor, i.e. exactly
  the pairs whose changes the consumer evaluates.

Changing one consumer's mode therefore moves only that consumer's
fingerprint (plus the aggregate `consumer` domain fingerprint); schema,
partition, and policy domain fingerprints, every pair fingerprint, other
consumers' fingerprints, and all affected-pair sets remain unchanged.

The breakdown is embedded in the result as `fingerprint_breakdown` and
persisted in the `runs` table in its own `fingerprint_breakdown_json` column.

## 6. Diff engine

For each adjacent version pair, the engine matches fields in two passes:

1. Same field name.
2. Explicit rename: same non-null `field_id`, or a declared alias on either
   side linking the old and new names.

There is no fuzzy name matching, similarity score, or positional guess. If
multiple explicit rename candidates exist, the lexicographically smallest new
name is selected deterministically.

Matching fields are compared for nullability, default presence/value,
constraints, and type. Unmatched fields become `drop_field` or `add_field`
changes. Explicit matches with different names become `rename_field` changes.

Change classes:

- `add_field`, `drop_field`, `rename_field`.
- `widen_type`, `narrow_type`, `type_change`.
- `nullable_to_true`, `nullable_to_false`.
- `default_added`, `default_removed`, `default_changed`.
- `constraint_change`.
- `field_order_change` for shared fields whose relative order changes.
- Recursive nested struct changes plus one `struct_field_change` aggregate.
- `element_type_change` for list or map element/key/value signature changes.
- `decimal_change` with precision/scale direction.
- `timestamp_unit_change`.
- `partition_change`.

Each change has a stable change ID:

```text
<table_id>:<from_version>-><to_version>:<field_path>:<change_type>
```

`table_id` is a sanitized table identifier. `$fields` is used for top-level
order changes and `$partition_spec` for partition changes. Evidence points into
the declarative structure, for example:

```text
tables.demo_lake_events.versions.v2.fields.user_id.type
tables.demo_lake_events.versions.v3.partition_spec
```

Changes are sorted by `(field_path, change_type, change_id)` before evaluation.

## 7. Compatibility evaluation

For each consumer:

1. Determine its anchor: the highest declared read/write version index.
2. Include changes whose `from_version` index is at or after that anchor.
3. Evaluate every included change and create a finding.
4. If no changes are included, create synthetic `SG-COMP-000` PASS finding.
5. Consumer decision is the strongest finding.

The anchor model intentionally keeps older consumers exposed to every later
transition until they adopt a newer version. A consumer anchored at `v1` is
therefore evaluated against `v1->v2` and `v2->v3`; a consumer anchored at `v2`
is evaluated only against `v2->v3`. This is a monotone "not yet migrated"
assumption, not a claim that the old reader literally consumes new data.

Mode-specific rules and policy precedence are in
[COMPATIBILITY.md](COMPATIBILITY.md).

## 8. Partitions

The partition engine compares two normalized declarations:

- Added, removed, and reordered keys.
- Per-column transform changes.
- Bucket counts and truncate widths.
- Sort columns and file format.

It emits one `partition_change` per version pair when anything differs, with
structured before/after details. It never opens a data file or checks physical
layout.

Partition changes are special-cased ahead of consumer modes. By default they
require review. A policy may forbid them, a consumer may allow them, and a
consumer severity override may set an explicit severity. They are therefore
not silently ignored even for a consumer using mode `none`.
A consumer using `none` bypasses policy and overrides for non-partition
changes, but not this partition path.

## 9. Planner

The planner turns findings and version transitions into advisory steps:

1. For every pair, a reader-publish step.
2. If any writer is pending for that pair, a writer-cutover step immediately
   after the reader step.
3. A final non-reversible compatibility-retirement step.

Readers are ordered before writers. Affected consumers are computed from the
anchor model: a consumer is pending for a transition when its anchor is at or
before the pair's from-version. A consumer appears in later transitions until
it is treated as migrated. Status is `blocked`, `review`, or `ready` from the
relevant findings. Blocking reasons contain consumer, message, rule ID, and
change ID.

Rollback hints preserve:

- The pair and target rollback version.
- Fields that must be retained (dropped fields and both names of renamed
  fields).
- Consumers with non-PASS findings that should roll back first.
- The last plan step associated with that pair.

The final hint covers all pairs and lists all non-PASS consumers. Actions are
descriptive strings only. The tool never generates or runs `ALTER`, `DROP`, or
other DDL.

## 10. Result and reports

`build_result` assembles a single Python dictionary containing report schema
version, tool/rules versions, run ID, decision, case metadata, input
fingerprint, the hierarchical `fingerprint_breakdown`, versions, pairs,
changes, policy, consumers and findings, matrix, summary, blocked/review
items, migration steps, rollback hints, and a scope disclaimer.

The JSON renderer applies canonical indentation and key sorting. Markdown uses
stable tables and escaping. HTML uses Jinja autoescaping and embeds package CSS
inline. None of the three renderers reads the clock. The same stored result
always renders to the same bytes.

## 11. Ledger

SQLite is used as a local, zero-server ledger. The schema is normalized enough
to support recent/detail/filter queries while retaining full JSON payloads for
report reproduction.

The unique key
`(case_id, input_fingerprint, tool_version, rules_version)` provides
idempotency. On a repeated identical run the stored result is compared to the
new canonical serialization:

- Same bytes: reuse, bump attempt count, refresh report paths and last-seen.
- Different bytes: fail with `SG-LEDGER-001` rather than mutate history.

Before either comparison, the stored hierarchical fingerprint breakdown is
reverse-checked against the recomputed one. Run reuse keys on the full input
fingerprint alone; if that hash matches but the sub-fingerprint decomposition
differs, canonicalization has collapsed semantically different inputs in some
domain, and the ledger raises `SG-LEDGER-004` instead of silently reusing the
run. The check is skipped only for rows written before the breakdown column
existed (the column is added lazily via `ALTER TABLE`), where the byte-level
`SG-LEDGER-001` guard still applies.

All values are parameterized. Connections are explicitly closed with
`contextlib.closing`, including on Windows, so temporary databases can be
removed by tests.

Ledger timestamps are intentionally outside report bodies. This keeps reports
reproducible while preserving operational recency in the index and ledger API.

## 12. HTTP and startup safety

`create_app` wires one `CaseLoader`, one `Ledger`, and one `RunService` into the
Flask extension registry. Blueprints expose health and versioned API routes.
Errors are converted to a stable JSON envelope for `/api/` paths.

`__main__.main` parses `serve`, validates host and port, then creates the app.
Validation rejects wildcard and non-loopback addresses before app creation, so
a rejected bind cannot accidentally start a service. The server runs without
debug mode or the reloader.

There is no authentication or TLS. That is appropriate only for the local,
single-user context; see [DECISIONS.md](DECISIONS.md).

## 13. Failure behavior

Validation failures are expected inputs, not crashes. Each parser raises
`SchemaGateError` with a stable code, a human-readable message, and structured
details. The API maps codes to HTTP statuses. Unexpected exceptions remain
server errors and are not disguised as validation outcomes.

The tests cover representative failures for missing files, duplicate fields,
invalid types, duplicate versions, unknown modes, empty consumers, unknown
cases, path escapes, unknown runs, invalid formats, invalid limits, and ledger
conflicts.

## 14. Test strategy

- Unit tests for type normalization, schema parsing, canonical JSON, diff
  classes, partition rules, compatibility precedence, and planner behavior.
- Fixture-based integration tests for both demonstration cases.
- Golden JSON and Markdown reports with newline normalization.
- HTML assertions for inline CSS, required sections, stable repeated output,
  and absence of external protocols.
- HTTP tests with Flask's local test client.
- Offline guard that monkeypatches socket creation/connection on business paths.
- CLI tests for wildcard/no-loopback rejection and loopback defaults.
- Ledger tests for idempotency, attempts, report bytes, cross-case isolation,
  parameterization, and conflicts.
- Line-budget and single-file limits are checked manually with `wc` as part of
  project acceptance because they constrain the repository rather than runtime
  behavior.

## 15. Extension points and limits

The type system is intentionally finite. Adding a type requires updates to
normalization, signatures, policy vocabulary, and tests. Adding a change type
requires diff emission, compatibility rules for each mode, docs, and fixtures.

The engine does not model column-level masking, partitioning by expression,
schema-registry subjects, or engine-specific DDL. Those are outside the local
declarative boundary.

The fixtures and tests are synthetic. No production endpoints, credentials,
domains, or records are used.
