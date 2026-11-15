# Architecture decisions and trade-offs

This is a lightweight decision record for the choices that shape behavior.
Each entry states the decision, the alternatives considered, and the cost that
is accepted.

## ADR-001: Declarative metadata only, no data-plane access

**Decision.** SchemaGate reads YAML manifests, schema versions, contracts,
partition specs, and policies. It never opens Parquet/ORC/Avro files, samples
rows, queries catalogs, or executes DDL.

**Alternatives.** Read file footers to verify physical types; query a catalog
for authoritative state; run a shadow query to estimate breakage.

**Consequences.** The tool is fully offline, cheap, and reproducible, and it
cannot detect drift between declared metadata and actual files. That limitation
is explicit in reports and in the scope disclaimer. Physical verification
belongs to a separate data-quality or catalog-reconciliation system.

## ADR-002: Renames require explicit identity or aliases

**Decision.** Two fields are considered a rename only when they share a
non-null `field_id`, or when a declared alias on either side links the names.
No similarity metric, edit distance, or positional heuristic is used.

**Alternatives.** Fuzzy name matching, type+position matching, or treating any
drop/add pair as a rename.

**Consequences.** A real rename without declared identity is reported as one
drop plus one add. That can overstate risk, but it never invents a rename and
never hides a destructive change under a guessed correspondence. Deterministic
tie-breaking selects the lexicographically smallest explicit candidate if
multiple matches exist.

## ADR-003: Consumer anchor is the highest declared version

**Decision.** A consumer's anchor is the maximum index among all declared read
and write versions. Every change whose `from_version` is at or after that
anchor is evaluated for the consumer.

**Alternatives.** Evaluate only pairs that touch an explicitly declared
version; evaluate only the latest pair; require separate per-version contracts.

**Consequences.** An old consumer remains exposed to later transitions until it
declares a newer version. This is conservative and matches the practical
question "has this consumer adopted far enough to be safe?" It can flag a
consumer that is not actually scheduled to read the newest schema, and it
requires consumers to declare a current anchor to leave the exposure set. It
also avoids a silent gap where a consumer declares only `v1` and the tool
ignores `v2->v3`.

## ADR-004: Fingerprints preserve meaningful order and sort unordered sets

**Decision.** The input fingerprint preserves manifest version order, declared
field order, nested field order, partition-key order, and sort-column order.
It sorts consumers, consumer version lists, widening pairs, policy lists,
aliases, and severity overrides. Schema fingerprints sort field paths and omit
declaration order.

**Alternatives.** Sort everything for maximal stability; preserve everything;
hash raw file bytes.

**Consequences.** Reformatting YAML or reordering mapping keys does not change
the fingerprint, while a meaningful field reorder does. Schema fingerprints
remain stable under field reordering, letting the diff report the order change
separately from a type change. The two fingerprint notions must not be confused;
the report exposes both.

## ADR-005: Partition changes bypass mode but never bypass policy

**Decision.** A partition change is evaluated ahead of `backward`, `forward`,
`full`, and `none`. Policy forbidden means BLOCK; a consumer override is
honored; an explicit allow is PASS; otherwise REVIEW.

**Alternatives.** Treat partition changes like ordinary changes, including
mode rules, or ignore them when data files are out of scope.

**Consequences.** Changing partitioning is never silent, including for a
record-only consumer. Consumers must explicitly accept the layout change. The
cost is that mode-specific nuance is unavailable for partition changes and that
`review_changes: [partition_change]` is a no-op because the default is already
REVIEW. The special case is documented and tested.

## ADR-006: Mode `none` short-circuits non-partition policy

**Decision.** For non-partition changes, a consumer with mode `none` receives
`PASS` and rule `SG-COMP-400` before forbidden/review lists and overrides are
consulted. Partition changes still follow ADR-005.

**Alternatives.** Let policy forbidden override `none`; omit `none` consumers
from evaluation entirely; reject `none` in policy-governed cases.

**Consequences.** `none` is a deliberate record-only escape hatch. A policy
cannot force a `none` consumer to block an ordinary schema change. The case can
still be REVIEW/BLOCK because another consumer disagrees, and partition changes
remain protected. Teams that want policy to be absolute should not use `none`.

## ADR-007: Policy forbidden cannot be overridden; consumer overrides are explicit

**Decision.** For non-partition changes, policy forbidden converts the outcome
to BLOCK after the base rule and before any consumer override. A consumer
override can lower or raise the base severity only when the change is not
forbidden, and the outcome is recorded as `SG-POL-003` with the base severity.
Policy review promotes only a base PASS.

**Alternatives.** Let consumer overrides always win; let policy forbidden be
downgradable with a justification field; merge policy and consumer severity by
maximum.

**Consequences.** The policy is a hard ceiling and the consumer contract is a
local trade-off. Overrides remain visible in reports instead of silently
mutating the matrix. A consumer can lower a base BLOCK to PASS, which is
intentional but can hide risk from the aggregate; reviewers can still see the
`SG-POL-003` finding. Policy review cannot downgrade, so it is safe to add
review pressure without weakening existing rules.

## ADR-008: Run identity is deterministic and clock-free

**Decision.** The run ID is derived from a canonical SHA-256 of case ID, input
fingerprint, tool version, rules version, and report schema version. Reports
contain no generation timestamp. Ledger timestamps exist only in ledger
metadata.

**Alternatives.** UUIDs per request; timestamp-based IDs; timestamps in reports
for auditability.

**Consequences.** The same input reproduces the same run and byte-identical
reports, which makes golden tests and incident comparison practical. Re-running
is idempotent rather than creating duplicate history. The ledger cannot order
two literally identical runs except by attempt count and last-seen time, and
report bodies cannot show when a decision was made; the ledger detail supplies
that separately.

## ADR-009: Ledger idempotency refuses conflicts instead of overwriting

**Decision.** A unique key
`(case_id, input_fingerprint, tool_version, rules_version)` identifies a run.
Identical canonical result bytes reuse the row and increment `attempt_count`.
Different bytes under the same key raise `SG-LEDGER-001`.

**Alternatives.** Upsert and overwrite evidence; insert a new row every time;
store only the latest result.

**Consequences.** A code or environment defect cannot silently rewrite prior
evidence under an unchanged semantic input. The attempt count records repeated
execution. A legitimate deterministic-output change requires bumping the tool,
rules, or report schema version, which is also good provenance practice.
Storage grows with distinct inputs rather than with retries. The
sub-fingerprint reverse check (`SG-LEDGER-004`, see ADR-017) adds a second
guard that fires even when the full hash collides.

## ADR-010: SQLite for the local ledger

**Decision.** Use the Python standard library `sqlite3` with a local file,
normalized detail tables, and parameterized SQL.

**Alternatives.** JSON files; an embedded server database; an external
warehouse; an in-memory store.

**Consequences.** There is no service to install or configure, and queries are
transactional and indexable. Concurrency is limited to the SQLite model, which
is acceptable for a single-user loopback tool. There is no remote ledger, no
multi-writer coordination, and no retention policy beyond what operators do to
the file. All SQL values are passed as parameters, and every connection is
explicitly closed so Windows can delete temporary databases in tests.

## ADR-011: Plain Flask development server, loopback only

**Decision.** Use Flask's built-in server with debug and reloader disabled,
bound only to explicitly validated loopback addresses. Reject wildcard hosts
before app creation.

**Alternatives.** Waitress/Gunicorn; a production WSGI deployment with auth and
TLS; binding all interfaces by default.

**Consequences.** The one-command local experience is simple and has no extra
dependency. The server is not hardened for hostile networks, authentication,
multi-tenancy, or high concurrency. Binding to `0.0.0.0` is refused rather than
warned about, and the README repeats the local-only scope. A production
deployment would need a real WSGI server, access control, TLS, request limits,
and logging.

## ADR-012: Self-contained HTML with no scripts

**Decision.** Render HTML with Jinja autoescaping, inline package CSS, no
JavaScript, no charts, and no external URLs.

**Alternatives.** A small SPA; a chart library from a CDN; server-side images.

**Consequences.** Reports open offline, are easy to archive, and cannot phone
home. The UI is static and the index page is minimal. Visualization is limited
to tables and status text; anything richer would increase dependencies and
offline risk.

## ADR-013: Advisory planner instead of generated DDL

**Decision.** Produce ordered reader/writer/retirement steps, affected
consumers, prerequisites, blockers, reversibility flags, and rollback hints.
Do not generate or run `ALTER`, `DROP`, or other engine-specific statements.

**Alternatives.** Emit engine-specific DDL; integrate with a workflow engine;
mutate the schema automatically.

**Consequences.** The output is engine-neutral and safe to attach to a review.
Operators still translate the plan into their own engine's procedure. The
planner cannot guarantee physical execution details, locking, or backfills;
those are outside the declarative gate. Rollback hints name retained fields and
consumers to roll back first, but they are not executable runbooks.

## ADR-014: Finite, explicit type system

**Decision.** Support a fixed set of primitives plus `struct`, `list`, and
`map`, with canonical aliases and deterministic signatures. Reject unknown
types at load time.

**Alternatives.** Accept arbitrary engine type strings; build a full SQL type
parser; infer types from data files.

**Consequences.** Classification and widening rules are explainable and stable.
An engine-specific type outside the vocabulary cannot be represented without
extending the tool. That is preferable to silently classifying unknown types
as compatible or incompatible. Extending the type system is a code and rules
version change.

## ADR-015: One-file-per-case discovery with no runtime upload

**Decision.** Registered cases are direct child directories of the fixtures
root containing `manifest.yaml`. There is no endpoint to upload or name a case
path.

**Alternatives.** Accept arbitrary filesystem paths from API callers; support
ZIP uploads; scan recursively.

**Consequences.** The attack surface is small, case IDs are stable, and a
request cannot traverse outside the case directory. New cases require a file
change and a restart. Recursive discovery is intentionally avoided, as is
symlink escape after path resolution. Only the configured root is trusted, and
even within it, every referenced file must resolve beneath its case directory.

## ADR-016: Test the offline guarantee at the socket boundary

**Decision.** In addition to static avoidance of network clients, an automated
test monkeypatches socket creation and connection and runs the loader, diff,
evaluation, planning, reporting, and ledger paths. CLI tests also assert
wildcard and non-loopback rejection.

**Alternatives.** Rely only on a dependency audit; run the service without a
network namespace; assert only that HTTP libraries are not imported.

**Consequences.** The guarantee is tested at runtime for the business paths.
The test does not sandbox the test runner itself, and a new dependency could
still attempt network access outside the covered paths. The combination of
socket-level tests, local-only code, and startup host validation is stronger
than documentation alone.

## ADR-017: Hierarchical sub-fingerprints explain impact without changing identity

**Decision.** Alongside the full input fingerprint, every run carries a
`fingerprint_breakdown`: one sub-fingerprint per declaration domain (schema,
partition, consumer, policy), one per adjacent version pair covering both
schemas and the pair's partition specs, and one per consumer covering its full
declaration (mode, read/write versions, widening, partition permission,
severity overrides). Each consumer entry lists its anchor version and the pair
IDs that affect it, computed with the same anchor/`declared_versions` rule as
`affected_changes`. The run ID remains a function of the full input fingerprint
only. On ledger reuse the recomputed breakdown must equal the stored
breakdown; a mismatch under an identical full hash raises `SG-LEDGER-004`.

**Alternatives.** Recompute impact by diffing stored reports between runs;
derive the run ID from the sub-fingerprint collection; skip the reverse check
and trust the full hash; compute pair fingerprints per consumer.

**Consequences.** Reviewers see precisely which declaration changed and which
consumers and pairs it reaches — for example, flipping one consumer's mode
moves only that consumer's fingerprint and leaves schema, partition, policy,
pair fingerprints, and every affected-pair set untouched. Because sub-fingerprints
reuse the full payload's canonical builders, a divergence between the stored
breakdown and a recomputed one for the same full hash exposes a
canonicalization blind spot instead of silently reusing stale evidence. Pair
fingerprints stay global (not per consumer), so pair identity reflects only
schema and partition semantics; consumer reach is expressed separately through
`affected_pair_ids`.
