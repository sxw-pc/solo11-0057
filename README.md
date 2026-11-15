# SchemaGate / 模式演进闸

SchemaGate is a **local, offline, deterministic admission gate for declarative
lakehouse table schema evolution**. It reads local table schema versions,
consumer compatibility contracts, partition specifications, and a case policy;
computes a structured diff; evaluates every consumer against every affected
change; and returns `PASS`, `REVIEW`, or `BLOCK` with rule-level evidence.

The primary product form is a loopback HTTP service. A small command-line entry
point starts that service. There is no hosted control plane, no catalog
connection, and no DDL execution.

## What it does

1. Loads a registered local case from `fixtures/cases/<case_id>/`.
2. Validates and canonicalizes schemas, contracts, partitions, and policy.
3. Computes a stable input fingerprint and a derived run ID.
4. Diffs each adjacent schema version pair, including nested structs,
   collection element types, field order, defaults, nullability, constraints,
   decimals, timestamp units, and partition specifications.
5. Applies consumer compatibility modes and policy precedence.
6. Aggregates consumer verdicts as `BLOCK > REVIEW > PASS`.
7. Produces JSON, Markdown, and self-contained HTML reports.
8. Records the result in a local SQLite ledger idempotently.

Everything is synthetic and local. The two demonstration cases are
`lake_events` (three versions, three consumers, conflicting contracts, `BLOCK`)
and `lake_orders` (two versions, one consumer, partition layout change,
`REVIEW`).

## Scope and non-goals

**In scope**

- Declarative schema-version diffs and explainable classifications.
- Consumer compatibility contracts with `backward`, `forward`, `full`, and
  `none` modes.
- Policy `forbidden_changes` and `review_changes` lists, plus per-consumer
  severity overrides and declared widening pairs.
- Declarative partition-key, transform, bucket, truncate, sort-column, and
  file-format metadata comparison.
- Advisory migration ordering and rollback hints.
- Deterministic reports and a local decision ledger.
- A loopback-only JSON API and a minimal local HTML home page.

**Explicitly out of scope**

- Catalog or Schema Registry connectivity (no Hive, Glue, Iceberg, Hudi, or
  Delta integration).
- Reading Parquet, ORC, Avro, or any data files; sampling, lineage, quality,
  compaction, or time travel.
- DDL generation or execution, workflow engines, schedules, queues, alerting,
  RBAC, multi-tenancy, signatures, or audit attestations.
- SQL parsing or authoritative schema validation for a remote engine.
- SPA frameworks, charts, drag-and-drop editors, or external front-end assets.

## Quick start

Prerequisites: Python 3.11 or newer (the project is tested with Python 3.13).
Install dependencies once in an environment that already has the required
packages or local wheels available.

```bash
python -m venv .venv
# Windows Git Bash: source .venv/Scripts/activate
source .venv/bin/activate
pip install -e ".[dev]"
```

Start the local service (one command):

```bash
python -m schemagate serve --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/` for the local index, or check health:

```bash
curl -s http://127.0.0.1:8765/healthz
# {"status":"ok"}
```

Run the test suite (one command):

```bash
python -m pytest -q
```

The same entry points are available through the installed console script:
`schemagate serve` and `pytest -q`.

The default paths are repository-relative:

| Purpose | Default |
| --- | --- |
| Case fixtures | `fixtures/cases` |
| SQLite ledger | `var/schemagate.db` |
| Report files | `var/reports` |

They can be overridden with `--fixtures`, `--db`, and `--reports` or with
`SCHEMAGATE_FIXTURES`, `SCHEMAGATE_DB`, and `SCHEMAGATE_REPORTS`.

## Run your first case

```bash
curl -s -X POST http://127.0.0.1:8765/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"case_id":"lake_events"}'
```

The response includes the deterministic `run_id`, the aggregate `decision`, a
summary, attempt information, and report links. A second identical request
reuses the same run and increments `attempt_count`.

Read the reports with:

```bash
curl -s http://127.0.0.1:8765/api/v1/runs/<run_id>
curl -s "http://127.0.0.1:8765/api/v1/runs/<run_id>/report?format=json"
curl -s "http://127.0.0.1:8765/api/v1/runs/<run_id>/report?format=markdown"
curl -s "http://127.0.0.1:8765/api/v1/runs/<run_id>/report?format=html"
```

## Fixtures

```text
fixtures/cases/
├── lake_events/
│   ├── manifest.yaml
│   ├── policies/default.yaml
│   ├── schemas/{v1,v2,v3}.yaml
│   ├── partitions/{v1,v2,v3}.yaml
│   └── consumers/{bi_reader,etl_writer,ml_reader}.yaml
└── lake_orders/
    ├── manifest.yaml
    ├── policies/default.yaml
    ├── schemas/{v1,v2}.yaml
    ├── partitions/{v1,v2}.yaml
    └── consumers/finance_reader.yaml
```

Invalid input examples live in `fixtures/invalid/` and are used to assert
stable load errors. All names, table identifiers, and descriptions are
synthetic.

## Input contract

Only directories beneath the configured fixtures root that contain a
`manifest.yaml` are discovered. The manifest `case_id` must match its directory
name. Every schema, partition, consumer, and policy file is resolved beneath
that case directory; absolute paths and path escapes are rejected with
`SG-LOAD-013`. Consumers may reference only versions declared by the case.

### Manifest

```yaml
case_id: lake_example
table: demo.lake_example
description: Synthetic example table.
schema_versions:
  - version: v1
    file: schemas/v1.yaml
  - version: v2
    file: schemas/v2.yaml
partition_specs:
  - version: v1
    file: partitions/v1.yaml
  - version: v2
    file: partitions/v2.yaml
consumers:
  - file: consumers/example_reader.yaml
policy: policies/default.yaml
```

Schema versions are evaluated in the order declared in the manifest, so the
manifest defines the table's version history. Field order and partition-key
order are also meaningful and preserved.

### Schema version

```yaml
table: demo.lake_example
version: v2
fields:
  - name: event_id
    type: uuid
    nullable: false
    field_id: 1
  - name: event_time
    type: timestamp_ms
    nullable: false
  - name: amount
    type: decimal(12,2)
    nullable: true
    default: 0.00
  - name: payload
    type: struct
    fields:
      - name: action
        type: string
        aliases: [old_action]
      - name: devices
        type: list<string>
  - name: attributes
    type: map<string, int64>
```

Supported type spellings:

- Primitives: `boolean`, `int32`, `int64`, `float32`, `float64`, `string`,
  `binary`, `date`, `timestamp_ms`, `timestamp_us`, `decimal(p,s)`, `uuid`.
- Containers: `struct` with explicit `fields`; `list<T>`; `map<K,V>`.
- Common aliases such as `bigint`, `double`, `timestamp_millis`, and
  `timestamp_micros` normalize to the canonical spellings.
- Unknown types fail with `SG-LOAD-003`; structural validation failures use
  `SG-LOAD-008`.

Field attributes include `name`, `type`, `nullable`, optional `default`,
`field_id`, `aliases`, `doc`, `constraints`, and nested `fields`. Duplicate
field paths or duplicate non-null `field_id` values are rejected. Defaults and
constraints must be JSON-safe; mapping keys are normalized deterministically.
Aliases are sorted and deduplicated at load time.

### Consumer contract

```yaml
consumer_id: example_reader
table: demo.lake_example
read_versions: [v1]
mode: backward
allowed_type_widening: [int32->int64]
allow_partition_change: false
severity_overrides:
  field_order_change: PASS
```

`read_versions` and `write_versions` may be declared separately; at least one
must be present. Version order inside either list is canonicalized. The
**anchor version** is the highest declared version (or highest index in the
manifest) and is retained for provenance, but scoping uses separate
**read/write windows**: each adjacent version pair is `exposed`, `covered`, or
`excluded` per side. A reader's window opens at its highest declared read
version, while a writer's window keeps the inbound edge of its lowest
declared write version exposed (the cutover pair). Findings on later exposed
pairs are shadowed per side once an upstream pair blocks; a finding is flagged
`downgrade_shadow` and leaves the aggregated decision only when every
applicable side is shadowed. A read-only `v1` consumer therefore remains
exposed to every later transition until it adopts a newer version, while a
mixed consumer reading `v1` and writing `v3` gets distinct read and write
windows.

Modes:

- `backward`: reason about old consumers reading new schemas.
- `forward`: reason about new consumers reading old data.
- `full`: evaluate both directions and take the stronger severity.
- `none`: record non-partition changes as `PASS` for that consumer.

Partition changes are special-cased and are never silently ignored; see
[Compatibility](docs/COMPATIBILITY.md).

### Partition specification

```yaml
table: demo.lake_example
version: v2
partition_keys:
  - column: event_date
    transform: identity
  - column: region
    transform: bucket
    bucket_count: 16
sort_columns: [event_time]
file_format: parquet
```

Supported transforms are `identity`, `bucket`, and `truncate`. `bucket`
requires `bucket_count`; `truncate` requires `truncate_width`. The tool compares
declared metadata only. It does not inspect data-file layout.

### Policy

```yaml
policy_id: default
default_mode: backward
forbidden_changes: []
review_changes:
  - rename_field
  - field_order_change
  - decimal_change
```

`forbidden_changes` and `review_changes` must contain known change types and
must not overlap. A consumer may omit `mode`; it inherits the policy default.

## Decision model

Final decision aggregation is:

```text
BLOCK > REVIEW > PASS
```

Each finding contains a stable rule ID, change ID, consumer ID, evidence path,
message, and structured details. A consumer with no pending changes receives a
synthetic `SG-COMP-000` PASS finding so the matrix and ledger never have a
silent gap. Partition changes default to `REVIEW`, escalate to `BLOCK` when the
policy forbids them, and can be explicitly allowed or overridden by a consumer.

The full precedence and rule-ID matrix are in
[docs/COMPATIBILITY.md](docs/COMPATIBILITY.md). The architectural rationale is
in [docs/DESIGN.md](docs/DESIGN.md). Trade-offs are recorded in
[docs/DECISIONS.md](docs/DECISIONS.md).

## Reports

Every run writes three files under the reports directory:

- `<run_id>.json`: canonical indented JSON.
- `<run_id>.md`: deterministic Markdown with version pairs, changes,
  compatibility matrix, findings, blocked/review items, advisory migration
  steps, rollback hints, policy, and a scope notice.
- `<run_id>.html`: a self-contained HTML report with inline CSS.

Reports include the case ID and table, input fingerprint, schema versions and
schema fingerprints, version pairs, all changes, consumer findings, aggregate
decision, blocked and review items, migration steps, rollback hints, tool and
rules versions, and report schema version. They do **not** contain a generation
timestamp or any wall-clock-dependent value. HTML reports contain no external
URLs, CDN references, scripts, or remote styles.

## HTTP API

The service binds to loopback only. The default is `127.0.0.1:8765`.
`--host 0.0.0.0`, `--host ::`, and any non-loopback host are rejected before
the application is created, printing `SG-CONFIG-001` and exiting with status 2.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness; `{"status":"ok"}` |
| GET | `/` | Local HTML case and recent-run index |
| GET | `/api/v1/cases` | Registered case summaries |
| POST | `/api/v1/runs` | Run a registered case by `case_id` |
| GET | `/api/v1/runs/<run_id>` | Stored JSON report for a run |
| GET | `/api/v1/runs/<run_id>/report?format=json\|markdown\|html` | Stored report |
| GET | `/api/v1/ledger?case_id=...&limit=20` | Recent runs, optionally filtered by case |

`case_id` must name a registered case. Reports accept only `json`, `markdown`,
or `html`. `limit` must be an integer from 1 through 200. API errors use a
stable envelope:

```json
{"error":{"code":"SG-API-002","message":"unknown case_id","details":{"case_id":"missing"}}}
```

Status mapping and every error code are documented in
[docs/API.md](docs/API.md).

## Ledger and idempotency

The default ledger is `var/schemagate.db` (SQLite, created if absent). Tables:

- `cases`: observed case metadata and first/last seen timestamps.
- `runs`: run identity, input fingerprint, tool/rules versions, decision,
  stored result, report paths, attempt count, and timestamps.
- `version_pairs`: per-pair change counts.
- `changes`: full change payloads and evidence paths.
- `consumer_verdicts`: per-consumer decisions and full payloads.
- `findings`: rule outcomes with severity, rule ID, change ID, evidence path,
  and payload.

The idempotency key is
`(case_id, input_fingerprint, tool_version, rules_version)`. An identical rerun
returns the stored result, refreshes report paths and `last_seen_at`, and
increments `attempt_count`. A result mismatch for the same key fails with
`SG-LEDGER-001` instead of overwriting evidence. All SQL values are passed as
parameters. Ledger records may contain timestamps; report bodies do not.

## Determinism, offline behavior, and safety

- Runtime code performs no outbound network requests and imports no remote
  client. The only listening socket is the explicitly requested loopback HTTP
  server.
- Business paths are tested with Python sockets monkeypatched to fail.
- Input fingerprinting uses canonical UTF-8 JSON and SHA-256, preserving
  meaningful declared order while sorting unordered collections. Schema
  fingerprints sort field paths so pure ordering differences are caught by the
  diff rather than mistaken for type changes.
- The run ID is `run-<24 lowercase hex chars>` derived from the case ID,
  input fingerprint, tool version, rules version, and report schema version.
  It is not clock- or randomness-derived.
- Case file access is constrained beneath the registered case directory;
  absolute paths and `..` escapes are rejected.
- Request bodies must be JSON objects with a non-empty string `case_id`.
  Unknown cases, runs, formats, and ledger limits have stable codes.
- The HTML report uses only package-local CSS embedded into the document. It
  contains no external or protocol-relative URL.
- All text is UTF-8. Stable IDs use sanitized table IDs, manifest order,
  field paths, and change types.
- There are no credentials, certificates, tokens, real domains, real IP
  addresses, or production business records in the repository. `127.0.0.1` is
  the required loopback default, not an external address.

## Error codes

Loader validation uses stable `SG-LOAD-xxx` codes, including missing input
(`001`), duplicate field (`002`), invalid type (`003`), duplicate version
(`004`), unknown mode (`005`), empty consumers (`006`), unknown case (`007`),
structural validation (`008`), partition validation (`009`), consumer
validation (`010`), policy validation (`011`), duplicate case (`012`), and path
escape (`013`). API and ledger codes are listed in [docs/API.md](docs/API.md).

## Tests and line budget

```bash
python -m pytest -q
```

The suite contains 93 passing test cases, including:

- Golden JSON and Markdown reports.
- Self-contained HTML assertions and deterministic repeat rendering.
- Loader failure paths with stable codes.
- Diff coverage across every required change class.
- Compatibility modes, policy precedence, partition special cases, and
  consumer independence.
- Planner ordering, blockers, rollback fields, and absence of executable DDL.
- Ledger idempotency, attempt counting, report files, parameterized filters,
  conflicts, and cross-case isolation.
- API behavior and a socket-level offline guard.
- Rejection of wildcard and non-loopback bind addresses.

Code size is kept within the required budget. Measure Python, templates, and
local CSS (excluding documentation, fixtures, golden test data, caches, and the
ledger):

```bash
find src tests -type f \( -name '*.py' -o -name '*.html' -o -name '*.css' \) \
  -print0 | xargs -0 wc -l
```

Current total: **4,486 lines**, with the largest file at **263 lines**. The
implementation is therefore inside the 2,500–4,500-line range and the 300-line
per-file limit.

## Project layout

```text
src/schemagate/
├── __main__.py          # loopback-only service entry point
├── api.py               # /api/v1 endpoints and stable API errors
├── app.py               # Flask app factory, index, error handlers
├── canonical.py         # canonical JSON and fingerprints
├── compatibility.py     # modes, policy precedence, findings
├── config.py            # constants, loopback and port validation
├── contracts_parser.py  # consumer, policy, partition parsing
├── diff_engine.py       # structured schema diff
├── errors.py            # stable SchemaGateError
├── ledger.py            # SQLite ledger and idempotency
├── loader.py            # discovery, safe reads, case assembly
├── models.py            # immutable domain dataclasses
├── naming.py            # stable IDs and evidence paths
├── partition_rules.py   # declarative partition diff
├── planner.py           # advisory steps and rollback hints
├── report_*.py          # JSON, Markdown, HTML renderers
├── schema_parser.py     # schema field and type parsing
├── type_rules.py        # type normalization and widening
├── static/report.css    # inline-embedded local report CSS
└── templates/           # report and index templates

fixtures/cases/          # synthetic demonstration cases
fixtures/invalid/        # invalid-input examples
tests/                   # unit, integration, golden, offline tests
docs/                    # design, compatibility, API, decisions
```

## Documentation

- [Design and data flow](docs/DESIGN.md)
- [Compatibility rules](docs/COMPATIBILITY.md)
- [HTTP API and error catalogue](docs/API.md)
- [Architecture decisions and trade-offs](docs/DECISIONS.md)

## Acceptance checklist

```bash
python -m pytest -q
python -m schemagate serve --host 127.0.0.1 --port 8765
curl -s http://127.0.0.1:8765/healthz
curl -s http://127.0.0.1:8765/api/v1/cases
curl -s -X POST http://127.0.0.1:8765/api/v1/runs \
  -H "Content-Type: application/json" -d '{"case_id":"lake_events"}'
curl -s -X POST http://127.0.0.1:8765/api/v1/runs \
  -H "Content-Type: application/json" -d '{"case_id":"lake_orders"}'
```

Expected fixture outcomes are `BLOCK` for `lake_events` and `REVIEW` for
`lake_orders`. Repeating either POST returns the same `run_id` with a higher
`attempt_count`; the ledger can then be filtered by case.
