# HTTP API and error catalogue

## 1. Service startup

```bash
python -m schemagate serve --host 127.0.0.1 --port 8765
```

Defaults:

```text
host      127.0.0.1
port      8765
fixtures  <repository>/fixtures/cases
db        <repository>/var/schemagate.db
reports   <repository>/var/reports
```

Options and environment overrides:

| Option | Environment | Meaning |
| --- | --- | --- |
| `--fixtures` | `SCHEMAGATE_FIXTURES` | Fixtures root containing registered case directories. |
| `--db` | `SCHEMAGATE_DB` | SQLite ledger path; created if absent. |
| `--reports` | `SCHEMAGATE_REPORTS` | Directory for JSON, Markdown, and HTML report files. |
| `--host` | none | Must be a loopback spelling. |
| `--port` | none | Integer from 1 through 65535. |

`--host 0.0.0.0`, `--host ::`, and any non-loopback address are rejected
before the Flask application is created. The process prints
`SG-CONFIG-001: ...` to stderr and exits with status 2. The server never runs
the debugger or reloader.

The service has no authentication, TLS, RBAC, or remote access mode. It is a
single-user local tool and must remain bound to loopback.

## 2. Conventions

- All successful JSON responses are UTF-8 with `application/json`.
- API JSON is canonical and compact: sorted keys, compact separators, and a
  trailing newline. The JSON report format uses canonical indentation.
- Examples below are pretty-printed for readability; the wire format is
  compact unless the endpoint returns a JSON report.
- Paths begin with `/api/v1` unless noted.
- All report and ledger content is derived from local fixtures and the local
  SQLite file.
- Report bodies contain no timestamps. Ledger rows contain
  `created_at`/`last_seen_at` timestamps and may appear in `/api/v1/ledger`.
- Empty request bodies, malformed JSON, and non-object JSON are rejected.
- Unknown query parameters are ignored. Unknown fields in a run request are
  ignored; only `case_id` is used.

### Error envelope

Application errors on `/api/` paths use:

```json
{
  "error": {
    "code": "SG-API-002",
    "message": "unknown case_id",
    "details": {
      "case_id": "missing_case"
    }
  }
}
```

The response is canonical JSON plus a newline. `details` may be `{}`. The HTTP
status is determined from the code, not from the message text.

## 3. Endpoints

### 3.1 `GET /healthz`

Liveness only. It does not read the ledger or fixtures.

```http
GET /healthz HTTP/1.1
Host: 127.0.0.1:8765
```

```http
HTTP/1.1 200 OK
Content-Type: application/json

{"status":"ok"}
```

The literal response body is `{"status":"ok"}\n`.

### 3.2 `GET /`

Local HTML index. It lists registered cases and the ten most recently seen
ledger runs. It uses only the package-local stylesheet served at
`/static/report.css`. The page contains no external resources.

Any non-API path that is not `/`, `/healthz`, or a static asset returns
`404 Not found` as plain text.

### 3.3 `GET /api/v1/cases`

Returns registered case summaries sorted by `case_id`.

```http
GET /api/v1/cases HTTP/1.1
```

```json
{
  "count": 2,
  "items": [
    {
      "case_id": "lake_events",
      "consumer_count": 3,
      "description": "Synthetic clickstream table with conflicting downstream contracts.",
      "schema_count": 3,
      "table": "demo.lake_events",
      "versions": ["v1", "v2", "v3"]
    },
    {
      "case_id": "lake_orders",
      "consumer_count": 1,
      "description": "Synthetic order fact table with a partition layout change.",
      "schema_count": 2,
      "table": "demo.lake_orders",
      "versions": ["v1", "v2"]
    }
  ]
}
```

Only direct child directories containing `manifest.yaml` are registered. A
malformed manifest can make application startup fail; there is no endpoint for
adding cases at runtime.

### 3.4 `POST /api/v1/runs`

Loads, validates, diffs, evaluates, renders, and registers one case.

Request:

```http
POST /api/v1/runs HTTP/1.1
Content-Type: application/json

{"case_id":"lake_events"}
```

```json
{
  "attempt_count": 1,
  "case_id": "lake_events",
  "decision": "BLOCK",
  "links": {
    "report_html": "/api/v1/runs/run-ef77f565d234ac3b852cc19e/report?format=html",
    "report_json": "/api/v1/runs/run-ef77f565d234ac3b852cc19e/report?format=json",
    "report_markdown": "/api/v1/runs/run-ef77f565d234ac3b852cc19e/report?format=markdown",
    "self": "/api/v1/runs/run-ef77f565d234ac3b852cc19e"
  },
  "reused": false,
  "run_id": "run-ef77f565d234ac3b852cc19e",
  "summary": {
    "block_count": 12,
    "change_count": 17,
    "consumer_count": 3,
    "consumer_decisions": {
      "BLOCK": 3,
      "PASS": 0,
      "REVIEW": 0
    },
    "finding_count": 42,
    "pass_count": 14,
    "review_count": 16,
    "version_pair_count": 2
  }
}
```

The example's exact counts and run ID are illustrative; the deterministic
values are the ones recorded in the golden report. The response excludes the
full report to keep it small. Use the `links` or the report endpoints.

`case_id` handling:

- Must be a string with non-whitespace content after trimming.
- Is trimmed before lookup.
- Must be a registered case. Unknown IDs become `SG-API-002`, status 404.
- Loader validation failures preserve their `SG-LOAD-*` code and map to 422
  (except path escape, 400) or 404 for unknown case.
- A repeated identical case reuses the stored run, returns the same
  `run_id`, sets `reused: true`, and increments `attempt_count`.
- A conflicting stored result for the same idempotency key returns
  `SG-LEDGER-001`, status 409.
- Before reuse, the stored `fingerprint_breakdown` is reverse-checked
  against the recomputed sub-fingerprints. A matching full fingerprint with
  a divergent decomposition (canonicalization lost semantics in some domain)
  returns `SG-LEDGER-004`, status 500, instead of silently reusing the run.

Request failures:

| Condition | Code | Status |
| --- | --- | --- |
| No body, malformed JSON, or JSON that is not an object | `SG-API-001` | 400 |
| Missing/blank/non-string `case_id` | `SG-API-001` | 400 |
| Unregistered case | `SG-API-002` | 404 |
| Case file missing/invalid/structurally unsupported | various `SG-LOAD-*` | 422 |
| Case path is absolute or escapes its directory | `SG-LOAD-013` | 400 |
| Same idempotency key with different result bytes | `SG-LEDGER-001` | 409 |
| Same full fingerprint with a mismatched sub-fingerprint breakdown on reuse | `SG-LEDGER-004` | 500 |

### 3.5 `GET /api/v1/runs/<run_id>`

Returns the stored canonical JSON report for a run. Semantically this is the
same body as `/report?format=json`.

```http
GET /api/v1/runs/run-ef77f565d234ac3b852cc19e HTTP/1.1
```

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

The report object includes:

| Key | Meaning |
| --- | --- |
| `report_schema_version`, `tool_version`, `rules_version` | Compatibility and provenance identifiers. |
| `run_id` | Deterministic run identifier. |
| `decision` | Aggregate `PASS`, `REVIEW`, or `BLOCK`. |
| `case` | `case_id`, `table`, and `description`. |
| `input_fingerprint` | `sha256:<hex>` over the normalized semantic case input. |
| `versions` | Version labels, schema fingerprints, field counts, partition specs. |
| `version_pairs` | Adjacent pairs, change counts, change IDs, and a partition-change flag. |
| `changes` | Every change with IDs, evidence paths, and before/after details. |
| `policy` | Policy ID, default mode, forbidden and review lists. |
| `consumers` | Per-consumer mode, anchor, decision, and findings. |
| `compatibility_matrix` | Consumer rows by version pair, including `N/A`. |
| `summary` | Counts by severity and consumer. |
| `blocked_items`, `review_items` | Non-PASS findings with change summaries. |
| `migration_steps` | Advisory ordering, prerequisites, affected consumers, blockers, reversibility. |
| `rollback_hints` | Targets, retained fields, consumers to roll back first, associated step. |
| `disclaimer` | Scope notice, including no DDL and no network access. |

A missing run returns `SG-API-003`, status 404.

### 3.6 `GET /api/v1/runs/<run_id>/report`

Returns a stored or re-rendered report in a selected format.

| `format` | Content-Type | Body |
| --- | --- | --- |
| `json` (default) | `application/json` | Canonical indented JSON report. |
| `markdown` | `text/markdown` | Deterministic Markdown report. |
| `html` | `text/html` | Self-contained HTML report. |

```http
GET /api/v1/runs/run-ef77f565d234ac3b852cc19e/report?format=markdown HTTP/1.1
```

`format` is trimmed and lowercased. Any value outside `json`, `markdown`, or
`html` returns `SG-API-004`, status 400, before run lookup.

The HTML report embeds its CSS and contains no external script, image, font,
CDN, `http://`, or `https://` reference. The Markdown report includes version
pairs, changes, findings, the compatibility matrix, decision drivers, migration
steps, rollback hints, and policy metadata.

A missing run returns `SG-API-003`, status 404. Reports are regenerated from the
stored result, so even if the on-disk report file is removed, the endpoint
still serves identical content.

### 3.7 `GET /api/v1/ledger`

Returns recent ledger runs ordered by `last_seen_at` descending, then
`run_id` ascending.

```http
GET /api/v1/ledger?case_id=lake_events&limit=20 HTTP/1.1
```

```json
{
  "count": 1,
  "items": [
    {
      "attempt_count": 2,
      "case_id": "lake_events",
      "created_at": "2026-01-01T00:00:00+00:00",
      "decision": "BLOCK",
      "input_fingerprint": "sha256:...",
      "last_seen_at": "2026-01-01T00:01:00+00:00",
      "report_paths": {
        "html": "/local/path/var/reports/run-....html",
        "json": "/local/path/var/reports/run-....json",
        "markdown": "/local/path/var/reports/run-....md"
      },
      "rules_version": "2026.11.1",
      "run_id": "run-...",
      "summary": {
        "block_count": 12,
        "change_count": 17,
        "consumer_count": 3,
        "consumer_decisions": {"BLOCK": 3, "PASS": 0, "REVIEW": 0},
        "finding_count": 42,
        "pass_count": 14,
        "review_count": 16,
        "version_pair_count": 2
      },
      "table": "demo.lake_events",
      "tool_version": "0.1.0"
    }
  ]
}
```

Query parameters:

| Parameter | Required | Validation |
| --- | --- | --- |
| `case_id` | No | If present, must be a registered non-empty case ID. |
| `limit` | No | Default 20. String of 1–3 digits, integer 1–200. |

Errors:

| Condition | Code | Status |
| --- | --- | --- |
| Unknown or empty `case_id` | `SG-API-002` | 404 |
| Non-digit, negative, or more than three digits in `limit` | `SG-API-006` | 400 |
| Parsed `limit` outside 1–200 | `SG-API-006` | 400 |

Timestamps are stored as UTC ISO-8601 strings with second precision. They are
operational ledger metadata and do not affect report bodies. Paths in
`report_paths` are local filesystem paths; the API never serves arbitrary
files from them.

## 4. Status mapping

HTTP status selection for `SchemaGateError`:

| Code pattern / exact code | Status |
| --- | --- |
| `SG-API-001`, `SG-API-004`, `SG-API-006` | 400 |
| `SG-LOAD-013` | 400 |
| `SG-API-002`, `SG-API-003`, `SG-LOAD-007` | 404 |
| `SG-API-005` (unknown `/api/` route) | 404 |
| `SG-LEDGER-001` | 409 |
| Other `SG-LOAD-*` | 422 |
| Other `SG-LEDGER-*` | 500 |
| Any other application error | 500 |

`SG-CONFIG-001` is a CLI startup error and never an HTTP response. Unexpected
non-`SchemaGateError` exceptions are not converted into stable codes; the test
suite treats unhandled server errors as defects.

## 5. Error catalogue

### 5.1 API codes

| Code | Status | Meaning | Typical details |
| --- | --- | --- | --- |
| `SG-API-001` | 400 | Run request body is not a JSON object or `case_id` is invalid. | `{}` |
| `SG-API-002` | 404 | Unknown case in a run or ledger query. | `case_id` |
| `SG-API-003` | 404 | Unknown run ID. | `run_id` |
| `SG-API-004` | 400 | Unsupported report format. | `format` |
| `SG-API-005` | 404 | Unknown endpoint under `/api/`. | `{}` |
| `SG-API-006` | 400 | Invalid ledger `limit`. | `{}` |

### 5.2 Loader codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `SG-LOAD-001` | 422 | Missing/unreadable input file, invalid YAML, duplicate YAML key, or missing fixtures root. |
| `SG-LOAD-002` | 422 | Duplicate field name/path within a schema. Duplicate `field_id` values use `SG-LOAD-008`. |
| `SG-LOAD-003` | 422 | Unknown or malformed type expression. |
| `SG-LOAD-004` | 422 | Duplicate schema version label. |
| `SG-LOAD-005` | 422 | Unknown compatibility mode. |
| `SG-LOAD-006` | 422 | Case declares no consumers. |
| `SG-LOAD-007` | 404 | Unknown case ID; translated to `SG-API-002` on run creation. |
| `SG-LOAD-008` | 422 | Manifest, schema, or field structural validation failure. |
| `SG-LOAD-009` | 422 | Partition specification validation failure. |
| `SG-LOAD-010` | 422 | Consumer contract validation failure. |
| `SG-LOAD-011` | 422 | Policy validation failure. |
| `SG-LOAD-012` | 422 | Duplicate case ID during discovery. |
| `SG-LOAD-013` | 400 | Absolute path or path escape in a case file reference. |

Loader messages include a `details` object with the failing file, field,
version, or attribute where available. The exact code is stable; human-readable
messages may be expanded without changing the code.

Most loader codes arise while serving a run and use the HTTP status shown.
Discovery-time failures such as a missing fixtures root (`SG-LOAD-001`) or a
duplicate case ID (`SG-LOAD-012`) abort application construction and therefore
do not reach HTTP; they must be fixed before the service can start.

### 5.3 Ledger and config codes

| Code | HTTP | Meaning |
| --- | --- | --- |
| `SG-LEDGER-001` | 409 | The same idempotency key maps to different stored result bytes. The ledger refuses to overwrite evidence. |
| `SG-LEDGER-002` | 500 | Ledger path is empty. This is normally a startup/configuration failure. |
| `SG-LEDGER-003` | 500 | Invalid limit passed directly to the ledger layer; the HTTP API normally rejects it first as `SG-API-006`. |
| `SG-LEDGER-004` | 500 | On reuse, the recomputed hierarchical `fingerprint_breakdown` differs from the stored breakdown even though the full input fingerprint matches. Indicates canonicalization dropped semantics in some domain; the stored run is not reused and `attempt_count` is not incremented. Details carry `run_id` and `mismatched_sections`. |
| `SG-CONFIG-001` | n/a (exit 2) | Wildcard, blank, or non-loopback bind host; invalid port; startup validation failure. |

## 6. Examples with curl

All commands are local. `python -m schemagate serve` must already be running.

```bash
curl -s http://127.0.0.1:8765/healthz
curl -s http://127.0.0.1:8765/api/v1/cases
curl -s -X POST http://127.0.0.1:8765/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"case_id":"lake_events"}'
curl -s -X POST http://127.0.0.1:8765/api/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"case_id":"lake_orders"}'
curl -s "http://127.0.0.1:8765/api/v1/ledger?case_id=lake_orders&limit=5"
curl -s "http://127.0.0.1:8765/api/v1/runs/<run_id>/report?format=html"
```

The expected decisions are `BLOCK` for `lake_events` and `REVIEW` for
`lake_orders`. Repeating a POST returns the same run ID and a higher attempt
count.

Rejected start example:

```bash
python -m schemagate serve --host 0.0.0.0
# stderr: SG-CONFIG-001: refusing to bind to a wildcard address; use 127.0.0.1
# exit status: 2
```

No request or startup path makes an outbound network connection.

## 7. Determinism contract

For a fixed case, tool version, rules version, and report schema version:

- `POST /api/v1/runs` returns the same `run_id`.
- The JSON report is byte-identical across runs.
- Markdown and HTML reports are byte-identical across runs.
- Findings, changes, matrix cells, blocked/review ordering, migration steps,
  and rollback hints have stable ordering.
- Ledger `attempt_count` and timestamps may change; they are not part of the
  report body.
- Deleting the reports directory does not change the report endpoint's body;
  it regenerates from the stored result.

See [DESIGN.md](DESIGN.md) for the identity construction and
[COMPATIBILITY.md](COMPATIBILITY.md) for rule semantics.
