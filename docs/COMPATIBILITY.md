# Compatibility rules

This document is the authoritative description of how SchemaGate turns one
change plus one consumer contract plus the case policy into one
`PASS`/`REVIEW`/`BLOCK` outcome.

## 1. Scope and aggregation

Evaluation is driven by **per-direction windows**, not by a single shared
anchor. For each consumer and each adjacent version pair, SchemaGate records a
`window_verdicts` entry with separate read and write statuses:

- `exposed` — the pair sits on the open boundary of the declared window on
  that side and every change on it is evaluated for the consumer;
- `covered` — both endpoints of the pair lie inside the declared span, so the
  consumer has already adopted through the transition and no finding is
  produced on that side;
- `excluded` — no declaration on that side reaches the pair.

### 1.1 Read windows

A reader's window opens at its highest declared read version. For declared
read indices `r_min … r_max`:

```text
pair index < r_min          excluded
r_min <= pair index < r_max covered   (transitions already adopted)
pair index >= r_max         exposed   (the reader still faces the new schema)
```

A reader declaring only `v1` therefore stays exposed to every later pair; the
tool does not assume an old reader can safely consume a new schema merely
because its declared list is narrow.

### 1.2 Write windows

The write window is deliberately asymmetric. A writer cut over to a declared
version must survive the inbound edge of that version, so the cutover pair
stays `exposed` even though its target version is declared. For declared
write indices `w_min … w_max`:

```text
pair index < w_min - 1              excluded
pair index == w_min - 1             exposed  (the cutover inbound edge)
w_min <= pair index < w_max         covered
pair index >= w_max                 exposed  (future published schemas)
```

A write-only consumer declaring `v3` on a three-version case is excluded from
`v1->v2` and exposed only on the `v2->v3` cutover; a writer declaring both
`v2` and `v3` additionally treats `v2->v3` as covered.

A consumer may declare both sides (`read_versions` and `write_versions`); the
windows are computed independently and a pair is evaluated when it is exposed
on at least one side. The legacy **anchor** is still reported as the highest
declared version overall, alongside `read_anchor_version` and
`write_anchor_version`.

### 1.3 Downgrade shadows

When an exposed pair contains a `BLOCK` finding on one side, every later pair
that is *also exposed on that same side* is **shadowed** on that side: once a
migration is blocked, the later transitions cannot be reached, so their
findings are moot for that consumer.

Shadowing is a window-containment relation, not a severity maximum:

- the upstream pair and the downstream pair must both be exposed on the same
  side. A finding on a pair that is excluded or merely covered on that side
  cannot shadow anything, and an upstream pair outside the window leaves the
  downstream finding fully visible;
- read and write shadows are independent. A read-side `PASS` can never
  swallow a write-side `BLOCK`, and a read-side shadow does not hide the same
  finding on its write side;
- a finding carries `read_shadow` / `write_shadow` per applicable side and is
  flagged `downgrade_shadow: true` only when **every** side it applies to is
  shadowed. Only fully shadowed findings leave the blocking/review buckets
  and the consumer's aggregated decision.

Each evaluated change still produces one finding per consumer; the per-pair
`read_shadowed_change_ids` and `write_shadowed_change_ids` enumerate the mooted
change IDs, and ineffective findings are collected under
`downgrade_shadow_items`.

A consumer's decision is the strongest severity among its effective
(non-shadowed) findings:

```text
BLOCK > REVIEW > PASS
```

The case decision is the strongest consumer decision. If a consumer has no
exposed changes, a synthetic finding with rule `SG-COMP-000`, change ID
`no-change`, and `PASS` is emitted. The matrix uses `N/A` for version pairs
that are exposed on neither side; pairs exposed on both sides show the
strongest of the read and write verdicts.

## 2. Precedence

Evaluation has two paths.

### 2.1 Partition changes

For a change whose type is `partition_change`, the mode is not consulted.
Precedence is:

1. **Policy forbidden** → `BLOCK`, rule `SG-POL-001`.
2. **Consumer severity override** for `partition_change` → that severity, rule
   `SG-POL-003`. This branch is taken even when the override is `REVIEW` (the
   same severity as the default).
3. **Consumer `allow_partition_change: true`** → `PASS`, rule `SG-PART-002`.
4. Otherwise → `REVIEW`, rule `SG-PART-001`.

Consequences:

- A consumer with mode `none` still gets `REVIEW` for a partition change.
- Policy `forbidden_changes: [partition_change]` wins over both an explicit
  allow and a severity override.
- The policy `review_changes` list has no additional effect on partition
  changes because the default is already `REVIEW`.
- Partition behavior is identical for `backward`, `forward`, `full`, and
  `none`.

### 2.2 Non-partition changes

Precedence is:

1. Compute the base rule for the consumer's mode (`backward`, `forward`,
   `full`, or `none`).
2. If mode is `none`, return `PASS`, rule `SG-COMP-400`. Policy is **not**
   consulted. This deliberately records rather than blocks changes.
3. If the change type is in `forbidden_changes` → `BLOCK`, rule `SG-POL-001`.
4. If the consumer has a severity override for the change type and it differs
   from the base severity → the override severity, rule `SG-POL-003`.
5. If the base is `PASS` and the change type is in `review_changes` → `REVIEW`,
   rule `SG-POL-002`.
6. Return the base outcome.

Consequences:

- `forbidden_changes` is absolute for non-`none` consumers; an override cannot
  lift it.
- A consumer override can lower `BLOCK` to `REVIEW` or `PASS`, or raise a
  `PASS`/`REVIEW` to a stronger severity. It is an explicit contract-level
  trade-off and is always recorded under rule `SG-POL-003` with the base rule
  and severity in details.
- An override equal to the base severity does not change the rule ID; the base
  outcome is returned, except that a `PASS` base may still be promoted by
  `review_changes`.
- `review_changes` only promotes `PASS`. It never downgrades `REVIEW` or
  `BLOCK`.
- `none` short-circuits before policy for non-partition changes. This is why a
  policy that forbids `drop_field` can still produce a case `PASS` if every
  consumer uses `none`.

## 3. Backward and forward rule tables

`backward` reasons about existing consumers reading the new schema. `forward`
reasons about new consumers reading old data. The severity tables are
deliberately symmetric for most destructive changes, with the notable
difference for added defaults.

### 3.1 Field presence and naming

| Change | Backward | Rule | Forward | Rule |
| --- | --- | --- | --- | --- |
| Required field added | BLOCK | `SG-COMP-101` | BLOCK | `SG-COMP-201` |
| Optional field added | PASS | `SG-COMP-102` | PASS | `SG-COMP-202` |
| Field dropped | BLOCK | `SG-COMP-103` | BLOCK | `SG-COMP-203` |
| Explicit field rename | REVIEW | `SG-COMP-104` | REVIEW | `SG-COMP-204` |

"Required" means a newly added field with `nullable: false` and no usable
default (no default at all, or a default of `null`), reported in change details
as `required: true`. An optional field is `PASS`.
A declared rename is always review-worthy because both old and new names remain
relevant to at least one side of the transition.

### 3.2 Type changes

| Change | Backward | Rule | Forward | Rule |
| --- | --- | --- | --- | --- |
| Widen, consumer declared the pair | PASS | `SG-COMP-105` | PASS | `SG-COMP-205` |
| Widen, pair not declared | REVIEW | `SG-COMP-106` | REVIEW | `SG-COMP-206` |
| Narrow | BLOCK | `SG-COMP-107` | BLOCK | `SG-COMP-207` |
| Incompatible type change | BLOCK | `SG-COMP-108` | BLOCK | `SG-COMP-208` |
| Decimal precision/scale change | REVIEW | `SG-COMP-115` | REVIEW | `SG-COMP-215` |
| Timestamp unit change | BLOCK | `SG-COMP-116` | BLOCK | `SG-COMP-216` |
| Collection element/key/value type change | BLOCK | `SG-COMP-117` | BLOCK | `SG-COMP-217` |

Declared widening is per consumer and uses canonical type spellings, for
example `int32->int64`. The built-in widening relation classifies
`int32 -> int64`, `int32 -> float64`, and `float32 -> float64` as widening.
Other type changes are `narrow` or `type_change` as determined by the type
system. Decimal changes and timestamp-unit changes receive dedicated change
types before widening classification.

### 3.3 Nullability, defaults, and structure

| Change | Backward | Rule | Forward | Rule |
| --- | --- | --- | --- | --- |
| Nullable → non-nullable | BLOCK | `SG-COMP-110` | BLOCK | `SG-COMP-210` |
| Non-nullable → nullable | PASS | `SG-COMP-109` | PASS | `SG-COMP-209` |
| Default added | PASS | `SG-COMP-111` | REVIEW | `SG-COMP-211` |
| Default removed | REVIEW | `SG-COMP-112` | REVIEW | `SG-COMP-212` |
| Default value changed | REVIEW | `SG-COMP-113` | REVIEW | `SG-COMP-213` |
| Field order changed | REVIEW | `SG-COMP-114` | REVIEW | `SG-COMP-214` |
| Nested struct member changed | REVIEW | `SG-COMP-118` | REVIEW | `SG-COMP-218` |
| Constraint changed | REVIEW | `SG-COMP-119` | REVIEW | `SG-COMP-219` |

Default-added asymmetry is intentional. An old reader encountering a new
schema with an added default is generally tolerant, while a new reader that
expects a default may encounter old records without one. A default removal or
value change is review-worthy in both directions.

Nested member changes additionally produce specific leaf changes. The
`struct_field_change` aggregate is evaluated as REVIEW as well. The consumer
decision therefore reflects the strongest leaf severity and the aggregate.

### 3.4 Unknown change types

Any change type not covered by a mode table returns `REVIEW` with
`SG-COMP-999`. This is a defensive fallback; the loader rejects unknown change
types in policy lists and overrides, and the diff engine emits only the
documented classes.

## 4. Full mode

`full` evaluates both the backward and forward rule and takes the stronger
severity.

| Backward and forward | Result | Rule |
| --- | --- | --- |
| Both PASS | PASS | `SG-COMP-300` |
| Both REVIEW | REVIEW | `SG-COMP-301` |
| Both BLOCK | BLOCK | `SG-COMP-302` |
| Different | Stronger direction's severity and rule | that direction's rule |

When the directions differ, details still include both rule IDs and both
severities. Example: `default_added` is PASS backward and REVIEW forward, so
full mode returns REVIEW with rule `SG-COMP-211`.

Declared widening is consumer-wide and applies in both directions, so a
declared widening yields PASS and rule `SG-COMP-300` under full mode.

## 5. None mode

For non-partition changes, `none` returns `PASS` with `SG-COMP-400` and does not
consult forbidden changes, review changes, or severity overrides. This is a
record-only contract mode. It does not disable diffing, finding generation, or
reporting.

For partition changes, the special partition path in section 2.1 applies and
`none` receives the same partition verdict as every other mode.

A case where all consumers are `none` can therefore be `PASS` for ordinary
schema changes but `REVIEW` because of a partition change.

## 6. Policy behavior

A policy has:

- `default_mode`: inherited by consumers that omit `mode`.
- `forbidden_changes`: known change types that are absolutely blocked for all
  non-`none` consumers and all consumers for partition changes.
- `review_changes`: known change types that promote a base PASS to REVIEW.

The lists must be disjoint and contain known change types. Unknown values fail
with `SG-LOAD-011`.

Policy findings retain evidence:

| Rule | Meaning | Details |
| --- | --- | --- |
| `SG-POL-001` | Policy forbids the change | Base rule/severity except for partition path |
| `SG-POL-002` | Policy requires review of a base PASS | Base rule ID |
| `SG-POL-003` | Consumer override changed severity | Base rule ID, base severity; partition path records `base_rule_id: SG-PART-001` |

Policy evaluation is per change and per consumer. It does not rewrite the
change itself. Reports retain both the base rule and the policy rule where the
implementation captures it.

## 7. Worked examples

All examples are synthetic.

### 7.1 Required field under backward

Consumer `reader_a` declares `read_versions: [v1]`, `mode: backward`. The pair
`v2->v3` adds a non-null field without a default. The reader's read window
opens at `v1`, so both pairs are exposed and the change is in scope:

```text
SG-COMP-101, BLOCK, reader_a
required field added at customer_note
```

If the same addition were nullable, it would be `SG-COMP-102`, PASS.

### 7.2 Forbidden drop versus consumer override

Policy forbids `drop_field`. A consumer sets `drop_field: REVIEW`. For a
non-`none` consumer the result is `SG-POL-001`, BLOCK. The override cannot win.
If the policy did not forbid it, the override would produce `SG-POL-003`,
REVIEW.

### 7.3 Partition change under mode none

Consumer `archive_reader` uses mode `none` and does not allow partition
changes. A bucket-count change still yields `SG-PART-001`, REVIEW. If the same
consumer sets `allow_partition_change: true`, the result is `SG-PART-002`,
PASS. If policy forbids `partition_change`, both configurations yield
`SG-POL-001`, BLOCK.

### 7.4 Policy review of an optional addition

Consumer `probe_reader` is backward. Policy lists `add_field` in
`review_changes`. Adding an optional field is base PASS with
`SG-COMP-102`, then policy promotes it to REVIEW with `SG-POL-002`.

### 7.5 Full mode on default addition

Consumer `full_reader` uses `full`. Adding a default is PASS backward and
REVIEW forward. The result is REVIEW under `SG-COMP-211`. Dropping a field is
BLOCK in both directions, so it returns `SG-COMP-302`.

### 7.6 Mixed read/write contract and downgrade shadows

Consumer `ml_reader` declares `read_versions: [v1]` and `write_versions: [v3]`
on a three-version case. Its windows are:

```text
v1->v2: read exposed, write excluded
v2->v3: read exposed, write exposed (the v3 cutover inbound edge)
```

Suppose `v1->v2` already carries a read-side BLOCK and `v2->v3` drops a field
that is BLOCK on both directions. The `v2->v3` findings receive
`read_shadow: true` because an upstream read BLOCK exists inside the read
window, but `write_shadow: false` because no upstream pair is write-exposed.
Since the finding applies to both sides and remains effective on the write
side, `downgrade_shadow` stays `false`, the write BLOCK blocks the writer
cutover step, and a read-side PASS can never mask it.

## 8. Determinism

Rule choice is a pure function of the consumer contract, policy, and change.
There are no heuristics, randomness, or time-dependent paths. Findings are
ordered by the change ordering, consumers are sorted by ID, and the report
renders the same matrix for the same stored result.

## 9. Related documents

- Architecture and fingerprint identity: [DESIGN.md](DESIGN.md)
- HTTP surface and error mapping: [API.md](API.md)
- Rationale and trade-offs: [DECISIONS.md](DECISIONS.md)
