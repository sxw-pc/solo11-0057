# Schema evolution gate report: lake_events

- Case: `lake_events`
- Table: `demo.lake_events`
- Description: Synthetic clickstream table with conflicting downstream contracts.
- Overall decision: **BLOCK**
- Input fingerprint: `sha256:0d0ebbb8f9a5fc50d1bd304f21951b3a6aa18d685337dc447c0699ec360325b1`
- Tool version: `0.1.0`
- Rules version: `2026.11.1`
- Report schema version: `2`

## Versions

| Version | Fields | Schema fingerprint | Partition specification |
| --- | --- | --- | --- |
| v1 | 9 | `sha256:6e53b7640515ae87f02a7a0d3703150413f5f76149392534e74451fb10416a9c` | event_date:identity |
| v2 | 10 | `sha256:e499b3b209436a3cf1ad0201a12e86dffdd20dbdaf13353af9ed83e7c7964714` | event_date:identity, region:bucket(16) |
| v3 | 11 | `sha256:dc763146ad35a5a98a2f5b94a8ed8c27f785615d0cb16718f0f9f28797cfd7bd` | event_date:identity, region:bucket(32) |

## Version pairs

| Pair | From | To | Changes | Partition changed |
| --- | --- | --- | --- | --- |
| v1->v2 | v1 | v2 | 9 | yes |
| v2->v3 | v2 | v3 | 8 | yes |

## Changes

| Pair | Type | Field path | Change ID | Evidence | Summary |
| --- | --- | --- | --- | --- | --- |
| v1->v2 | field_order_change | $fields | demo_lake_events:v1->v2:$fields:field_order_change | tables.demo_lake_events.versions.v2.fields.$fields.order | field order changed for 8 shared field(s) |
| v1->v2 | partition_change | $partition_spec | demo_lake_events:v1->v2:$partition_spec:partition_change | tables.demo_lake_events.versions.v2.partition_spec | partition specification changed: added keys: region |
| v1->v2 | drop_field | debug_flag | demo_lake_events:v1->v2:debug_flag:drop_field | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | field dropped: debug_flag (boolean) |
| v1->v2 | add_field | ingest_source | demo_lake_events:v1->v2:ingest_source:add_field | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | field added: ingest_source (string) |
| v1->v2 | struct_field_change | payload | demo_lake_events:v1->v2:payload:struct_field_change | tables.demo_lake_events.versions.v2.fields.payload.fields | nested struct fields changed under payload: 1 path(s) |
| v1->v2 | add_field | payload.channel | demo_lake_events:v1->v2:payload.channel:add_field | tables.demo_lake_events.versions.v2.fields.payload.channel.presence | field added: payload.channel (string) |
| v1->v2 | add_field | region | demo_lake_events:v1->v2:region:add_field | tables.demo_lake_events.versions.v2.fields.region.presence | field added: region (string) |
| v1->v2 | rename_field | source_code | demo_lake_events:v1->v2:source_code:rename_field | tables.demo_lake_events.versions.v2.fields.source_code.name | field explicitly renamed: legacy_code -> source_code |
| v1->v2 | widen_type | user_id | demo_lake_events:v1->v2:user_id:widen_type | tables.demo_lake_events.versions.v2.fields.user_id.type | type widened for user_id: int32 -> int64 |
| v2->v3 | partition_change | $partition_spec | demo_lake_events:v2->v3:$partition_spec:partition_change | tables.demo_lake_events.versions.v3.partition_spec | partition specification changed: 1 transform change(s); sort columns changed |
| v2->v3 | decimal_change | amount | demo_lake_events:v2->v3:amount:decimal_change | tables.demo_lake_events.versions.v3.fields.amount.type | decimal changed for amount: decimal(10,2) -> decimal(12,2) (widen) |
| v2->v3 | timestamp_unit_change | event_time | demo_lake_events:v2->v3:event_time:timestamp_unit_change | tables.demo_lake_events.versions.v3.fields.event_time.type | timestamp unit changed for event_time: timestamp_ms -> timestamp_us |
| v2->v3 | nullable_to_true | ingest_source | demo_lake_events:v2->v3:ingest_source:nullable_to_true | tables.demo_lake_events.versions.v3.fields.ingest_source.nullable | nullable changed: ingest_source False -> True |
| v2->v3 | struct_field_change | payload | demo_lake_events:v2->v3:payload:struct_field_change | tables.demo_lake_events.versions.v3.fields.payload.fields | nested struct fields changed under payload: 1 path(s) |
| v2->v3 | drop_field | payload.device | demo_lake_events:v2->v3:payload.device:drop_field | tables.demo_lake_events.versions.v2.fields.payload.device.presence | field dropped: payload.device (string) |
| v2->v3 | add_field | session_id | demo_lake_events:v2->v3:session_id:add_field | tables.demo_lake_events.versions.v3.fields.session_id.presence | field added: session_id (string) |
| v2->v3 | element_type_change | tags | demo_lake_events:v2->v3:tags:element_type_change | tables.demo_lake_events.versions.v3.fields.tags.type | nested collection type changed for tags: list<string> -> list<int64> |

## Consumer compatibility matrix

| Consumer | Mode | Anchor | v1->v2 | v2->v3 | Overall |
| --- | --- | --- | --- | --- | --- |
| bi_reader | backward | v1 | BLOCK | BLOCK | BLOCK |
| etl_writer | forward | v1 | BLOCK | BLOCK | BLOCK |
| ml_reader | full | v3 | BLOCK | BLOCK | BLOCK |

## Read/write window verdicts

| Consumer | Pair | Read status/verdict | Write status/verdict | Read-shadowed changes | Write-shadowed changes |
| --- | --- | --- | --- | --- | --- |
| bi_reader | v1->v2 | exposed/BLOCK | excluded/N/A | 0 | 0 |
| bi_reader | v2->v3 | exposed/BLOCK | excluded/N/A | 8 | 0 |
| etl_writer | v1->v2 | excluded/N/A | exposed/BLOCK | 0 | 0 |
| etl_writer | v2->v3 | excluded/N/A | exposed/BLOCK | 0 | 8 |
| ml_reader | v1->v2 | exposed/BLOCK | excluded/N/A | 0 | 0 |
| ml_reader | v2->v3 | exposed/BLOCK | exposed/BLOCK | 8 | 0 |

## Findings

| Consumer | Severity | Rule | Change ID | Field path | Evidence | Message |
| --- | --- | --- | --- | --- | --- | --- |
| bi_reader | PASS | SG-POL-003 | demo_lake_events:v1->v2:$fields:field_order_change | $fields | tables.demo_lake_events.versions.v2.fields.$fields.order | consumer override sets field_order_change from REVIEW to PASS |
| bi_reader | REVIEW | SG-PART-001 | demo_lake_events:v1->v2:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v2.partition_spec | partition change requires review at $partition_spec |
| bi_reader | BLOCK | SG-COMP-103 | demo_lake_events:v1->v2:debug_flag:drop_field | debug_flag | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | backward mode blocks field removal at debug_flag |
| bi_reader | BLOCK | SG-COMP-101 | demo_lake_events:v1->v2:ingest_source:add_field | ingest_source | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | backward mode blocks a new required field at ingest_source |
| bi_reader | REVIEW | SG-COMP-118 | demo_lake_events:v1->v2:payload:struct_field_change | payload | tables.demo_lake_events.versions.v2.fields.payload.fields | nested struct fields changed at payload |
| bi_reader | PASS | SG-COMP-102 | demo_lake_events:v1->v2:payload.channel:add_field | payload.channel | tables.demo_lake_events.versions.v2.fields.payload.channel.presence | optional field added at payload.channel |
| bi_reader | PASS | SG-COMP-102 | demo_lake_events:v1->v2:region:add_field | region | tables.demo_lake_events.versions.v2.fields.region.presence | optional field added at region |
| bi_reader | REVIEW | SG-COMP-104 | demo_lake_events:v1->v2:source_code:rename_field | source_code | tables.demo_lake_events.versions.v2.fields.source_code.name | explicit field rename requires review at source_code |
| bi_reader | PASS | SG-COMP-105 | demo_lake_events:v1->v2:user_id:widen_type | user_id | tables.demo_lake_events.versions.v2.fields.user_id.type | allowed type widening at user_id |
| bi_reader | REVIEW | SG-PART-001 | demo_lake_events:v2->v3:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v3.partition_spec | partition change requires review at $partition_spec |
| bi_reader | REVIEW | SG-COMP-115 | demo_lake_events:v2->v3:amount:decimal_change | amount | tables.demo_lake_events.versions.v3.fields.amount.type | decimal precision or scale changed at amount |
| bi_reader | BLOCK | SG-COMP-116 | demo_lake_events:v2->v3:event_time:timestamp_unit_change | event_time | tables.demo_lake_events.versions.v3.fields.event_time.type | timestamp unit changed at event_time |
| bi_reader | PASS | SG-COMP-109 | demo_lake_events:v2->v3:ingest_source:nullable_to_true | ingest_source | tables.demo_lake_events.versions.v3.fields.ingest_source.nullable | nullable relaxation accepted at ingest_source |
| bi_reader | REVIEW | SG-COMP-118 | demo_lake_events:v2->v3:payload:struct_field_change | payload | tables.demo_lake_events.versions.v3.fields.payload.fields | nested struct fields changed at payload |
| bi_reader | BLOCK | SG-COMP-103 | demo_lake_events:v2->v3:payload.device:drop_field | payload.device | tables.demo_lake_events.versions.v2.fields.payload.device.presence | backward mode blocks field removal at payload.device |
| bi_reader | PASS | SG-COMP-102 | demo_lake_events:v2->v3:session_id:add_field | session_id | tables.demo_lake_events.versions.v3.fields.session_id.presence | optional field added at session_id |
| bi_reader | BLOCK | SG-COMP-117 | demo_lake_events:v2->v3:tags:element_type_change | tags | tables.demo_lake_events.versions.v3.fields.tags.type | collection element type changed at tags |
| etl_writer | REVIEW | SG-COMP-214 | demo_lake_events:v1->v2:$fields:field_order_change | $fields | tables.demo_lake_events.versions.v2.fields.$fields.order | field order changed at $fields |
| etl_writer | REVIEW | SG-PART-001 | demo_lake_events:v1->v2:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v2.partition_spec | partition change requires review at $partition_spec |
| etl_writer | BLOCK | SG-COMP-203 | demo_lake_events:v1->v2:debug_flag:drop_field | debug_flag | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | forward mode blocks field removal at debug_flag |
| etl_writer | BLOCK | SG-COMP-201 | demo_lake_events:v1->v2:ingest_source:add_field | ingest_source | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | forward mode blocks a new required field at ingest_source |
| etl_writer | REVIEW | SG-COMP-218 | demo_lake_events:v1->v2:payload:struct_field_change | payload | tables.demo_lake_events.versions.v2.fields.payload.fields | nested struct fields changed at payload |
| etl_writer | PASS | SG-COMP-202 | demo_lake_events:v1->v2:payload.channel:add_field | payload.channel | tables.demo_lake_events.versions.v2.fields.payload.channel.presence | optional field added at payload.channel |
| etl_writer | PASS | SG-COMP-202 | demo_lake_events:v1->v2:region:add_field | region | tables.demo_lake_events.versions.v2.fields.region.presence | optional field added at region |
| etl_writer | REVIEW | SG-COMP-204 | demo_lake_events:v1->v2:source_code:rename_field | source_code | tables.demo_lake_events.versions.v2.fields.source_code.name | explicit field rename requires review at source_code |
| etl_writer | PASS | SG-COMP-205 | demo_lake_events:v1->v2:user_id:widen_type | user_id | tables.demo_lake_events.versions.v2.fields.user_id.type | allowed type widening at user_id |
| etl_writer | REVIEW | SG-PART-001 | demo_lake_events:v2->v3:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v3.partition_spec | partition change requires review at $partition_spec |
| etl_writer | REVIEW | SG-COMP-215 | demo_lake_events:v2->v3:amount:decimal_change | amount | tables.demo_lake_events.versions.v3.fields.amount.type | decimal precision or scale changed at amount |
| etl_writer | BLOCK | SG-COMP-216 | demo_lake_events:v2->v3:event_time:timestamp_unit_change | event_time | tables.demo_lake_events.versions.v3.fields.event_time.type | timestamp unit changed at event_time |
| etl_writer | PASS | SG-COMP-209 | demo_lake_events:v2->v3:ingest_source:nullable_to_true | ingest_source | tables.demo_lake_events.versions.v3.fields.ingest_source.nullable | nullable relaxation accepted at ingest_source |
| etl_writer | REVIEW | SG-COMP-218 | demo_lake_events:v2->v3:payload:struct_field_change | payload | tables.demo_lake_events.versions.v3.fields.payload.fields | nested struct fields changed at payload |
| etl_writer | BLOCK | SG-COMP-203 | demo_lake_events:v2->v3:payload.device:drop_field | payload.device | tables.demo_lake_events.versions.v2.fields.payload.device.presence | forward mode blocks field removal at payload.device |
| etl_writer | PASS | SG-COMP-202 | demo_lake_events:v2->v3:session_id:add_field | session_id | tables.demo_lake_events.versions.v3.fields.session_id.presence | optional field added at session_id |
| etl_writer | BLOCK | SG-COMP-217 | demo_lake_events:v2->v3:tags:element_type_change | tags | tables.demo_lake_events.versions.v3.fields.tags.type | collection element type changed at tags |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v1->v2:$fields:field_order_change | $fields | tables.demo_lake_events.versions.v2.fields.$fields.order | full mode requires review for $fields |
| ml_reader | PASS | SG-PART-002 | demo_lake_events:v1->v2:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v2.partition_spec | consumer explicitly allows partition changes at $partition_spec |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v1->v2:debug_flag:drop_field | debug_flag | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | full mode blocks change at debug_flag |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v1->v2:ingest_source:add_field | ingest_source | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | full mode blocks change at ingest_source |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v1->v2:payload:struct_field_change | payload | tables.demo_lake_events.versions.v2.fields.payload.fields | full mode requires review for payload |
| ml_reader | PASS | SG-COMP-300 | demo_lake_events:v1->v2:payload.channel:add_field | payload.channel | tables.demo_lake_events.versions.v2.fields.payload.channel.presence | full mode is compatible for payload.channel |
| ml_reader | PASS | SG-COMP-300 | demo_lake_events:v1->v2:region:add_field | region | tables.demo_lake_events.versions.v2.fields.region.presence | full mode is compatible for region |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v1->v2:source_code:rename_field | source_code | tables.demo_lake_events.versions.v2.fields.source_code.name | full mode requires review for source_code |
| ml_reader | PASS | SG-COMP-300 | demo_lake_events:v1->v2:user_id:widen_type | user_id | tables.demo_lake_events.versions.v2.fields.user_id.type | full mode is compatible for user_id |
| ml_reader | PASS | SG-PART-002 | demo_lake_events:v2->v3:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v3.partition_spec | consumer explicitly allows partition changes at $partition_spec |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v2->v3:amount:decimal_change | amount | tables.demo_lake_events.versions.v3.fields.amount.type | full mode requires review for amount |
| ml_reader | REVIEW | SG-POL-003 | demo_lake_events:v2->v3:event_time:timestamp_unit_change | event_time | tables.demo_lake_events.versions.v3.fields.event_time.type | consumer override sets timestamp_unit_change from BLOCK to REVIEW |
| ml_reader | PASS | SG-COMP-300 | demo_lake_events:v2->v3:ingest_source:nullable_to_true | ingest_source | tables.demo_lake_events.versions.v3.fields.ingest_source.nullable | full mode is compatible for ingest_source |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v2->v3:payload:struct_field_change | payload | tables.demo_lake_events.versions.v3.fields.payload.fields | full mode requires review for payload |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v2->v3:payload.device:drop_field | payload.device | tables.demo_lake_events.versions.v2.fields.payload.device.presence | full mode blocks change at payload.device |
| ml_reader | PASS | SG-COMP-300 | demo_lake_events:v2->v3:session_id:add_field | session_id | tables.demo_lake_events.versions.v3.fields.session_id.presence | full mode is compatible for session_id |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v2->v3:tags:element_type_change | tags | tables.demo_lake_events.versions.v3.fields.tags.type | full mode blocks change at tags |

## Blocking items

| Consumer | Severity | Rule | Change ID | Field path | Evidence | Message |
| --- | --- | --- | --- | --- | --- | --- |
| bi_reader | BLOCK | SG-COMP-103 | demo_lake_events:v1->v2:debug_flag:drop_field | debug_flag | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | backward mode blocks field removal at debug_flag |
| bi_reader | BLOCK | SG-COMP-101 | demo_lake_events:v1->v2:ingest_source:add_field | ingest_source | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | backward mode blocks a new required field at ingest_source |
| etl_writer | BLOCK | SG-COMP-203 | demo_lake_events:v1->v2:debug_flag:drop_field | debug_flag | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | forward mode blocks field removal at debug_flag |
| etl_writer | BLOCK | SG-COMP-201 | demo_lake_events:v1->v2:ingest_source:add_field | ingest_source | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | forward mode blocks a new required field at ingest_source |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v1->v2:debug_flag:drop_field | debug_flag | tables.demo_lake_events.versions.v1.fields.debug_flag.presence | full mode blocks change at debug_flag |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v1->v2:ingest_source:add_field | ingest_source | tables.demo_lake_events.versions.v2.fields.ingest_source.presence | full mode blocks change at ingest_source |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v2->v3:payload.device:drop_field | payload.device | tables.demo_lake_events.versions.v2.fields.payload.device.presence | full mode blocks change at payload.device |
| ml_reader | BLOCK | SG-COMP-302 | demo_lake_events:v2->v3:tags:element_type_change | tags | tables.demo_lake_events.versions.v3.fields.tags.type | full mode blocks change at tags |

## Review items

| Consumer | Severity | Rule | Change ID | Field path | Evidence | Message |
| --- | --- | --- | --- | --- | --- | --- |
| bi_reader | REVIEW | SG-PART-001 | demo_lake_events:v1->v2:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v2.partition_spec | partition change requires review at $partition_spec |
| bi_reader | REVIEW | SG-COMP-118 | demo_lake_events:v1->v2:payload:struct_field_change | payload | tables.demo_lake_events.versions.v2.fields.payload.fields | nested struct fields changed at payload |
| bi_reader | REVIEW | SG-COMP-104 | demo_lake_events:v1->v2:source_code:rename_field | source_code | tables.demo_lake_events.versions.v2.fields.source_code.name | explicit field rename requires review at source_code |
| etl_writer | REVIEW | SG-COMP-214 | demo_lake_events:v1->v2:$fields:field_order_change | $fields | tables.demo_lake_events.versions.v2.fields.$fields.order | field order changed at $fields |
| etl_writer | REVIEW | SG-PART-001 | demo_lake_events:v1->v2:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v2.partition_spec | partition change requires review at $partition_spec |
| etl_writer | REVIEW | SG-COMP-218 | demo_lake_events:v1->v2:payload:struct_field_change | payload | tables.demo_lake_events.versions.v2.fields.payload.fields | nested struct fields changed at payload |
| etl_writer | REVIEW | SG-COMP-204 | demo_lake_events:v1->v2:source_code:rename_field | source_code | tables.demo_lake_events.versions.v2.fields.source_code.name | explicit field rename requires review at source_code |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v1->v2:$fields:field_order_change | $fields | tables.demo_lake_events.versions.v2.fields.$fields.order | full mode requires review for $fields |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v1->v2:payload:struct_field_change | payload | tables.demo_lake_events.versions.v2.fields.payload.fields | full mode requires review for payload |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v1->v2:source_code:rename_field | source_code | tables.demo_lake_events.versions.v2.fields.source_code.name | full mode requires review for source_code |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v2->v3:amount:decimal_change | amount | tables.demo_lake_events.versions.v3.fields.amount.type | full mode requires review for amount |
| ml_reader | REVIEW | SG-POL-003 | demo_lake_events:v2->v3:event_time:timestamp_unit_change | event_time | tables.demo_lake_events.versions.v3.fields.event_time.type | consumer override sets timestamp_unit_change from BLOCK to REVIEW |
| ml_reader | REVIEW | SG-COMP-301 | demo_lake_events:v2->v3:payload:struct_field_change | payload | tables.demo_lake_events.versions.v3.fields.payload.fields | full mode requires review for payload |

## Downgrade-shadowed findings (ineffective for this consumer)

| Consumer | Severity | Rule | Change ID | Field path | Evidence | Message |
| --- | --- | --- | --- | --- | --- | --- |
| bi_reader | REVIEW | SG-PART-001 | demo_lake_events:v2->v3:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v3.partition_spec | partition change requires review at $partition_spec |
| bi_reader | REVIEW | SG-COMP-115 | demo_lake_events:v2->v3:amount:decimal_change | amount | tables.demo_lake_events.versions.v3.fields.amount.type | decimal precision or scale changed at amount |
| bi_reader | BLOCK | SG-COMP-116 | demo_lake_events:v2->v3:event_time:timestamp_unit_change | event_time | tables.demo_lake_events.versions.v3.fields.event_time.type | timestamp unit changed at event_time |
| bi_reader | PASS | SG-COMP-109 | demo_lake_events:v2->v3:ingest_source:nullable_to_true | ingest_source | tables.demo_lake_events.versions.v3.fields.ingest_source.nullable | nullable relaxation accepted at ingest_source |
| bi_reader | BLOCK | SG-COMP-103 | demo_lake_events:v2->v3:payload.device:drop_field | payload.device | tables.demo_lake_events.versions.v2.fields.payload.device.presence | backward mode blocks field removal at payload.device |
| bi_reader | REVIEW | SG-COMP-118 | demo_lake_events:v2->v3:payload:struct_field_change | payload | tables.demo_lake_events.versions.v3.fields.payload.fields | nested struct fields changed at payload |
| bi_reader | PASS | SG-COMP-102 | demo_lake_events:v2->v3:session_id:add_field | session_id | tables.demo_lake_events.versions.v3.fields.session_id.presence | optional field added at session_id |
| bi_reader | BLOCK | SG-COMP-117 | demo_lake_events:v2->v3:tags:element_type_change | tags | tables.demo_lake_events.versions.v3.fields.tags.type | collection element type changed at tags |
| etl_writer | REVIEW | SG-PART-001 | demo_lake_events:v2->v3:$partition_spec:partition_change | $partition_spec | tables.demo_lake_events.versions.v3.partition_spec | partition change requires review at $partition_spec |
| etl_writer | REVIEW | SG-COMP-215 | demo_lake_events:v2->v3:amount:decimal_change | amount | tables.demo_lake_events.versions.v3.fields.amount.type | decimal precision or scale changed at amount |
| etl_writer | BLOCK | SG-COMP-216 | demo_lake_events:v2->v3:event_time:timestamp_unit_change | event_time | tables.demo_lake_events.versions.v3.fields.event_time.type | timestamp unit changed at event_time |
| etl_writer | PASS | SG-COMP-209 | demo_lake_events:v2->v3:ingest_source:nullable_to_true | ingest_source | tables.demo_lake_events.versions.v3.fields.ingest_source.nullable | nullable relaxation accepted at ingest_source |
| etl_writer | BLOCK | SG-COMP-203 | demo_lake_events:v2->v3:payload.device:drop_field | payload.device | tables.demo_lake_events.versions.v2.fields.payload.device.presence | forward mode blocks field removal at payload.device |
| etl_writer | REVIEW | SG-COMP-218 | demo_lake_events:v2->v3:payload:struct_field_change | payload | tables.demo_lake_events.versions.v3.fields.payload.fields | nested struct fields changed at payload |
| etl_writer | PASS | SG-COMP-202 | demo_lake_events:v2->v3:session_id:add_field | session_id | tables.demo_lake_events.versions.v3.fields.session_id.presence | optional field added at session_id |
| etl_writer | BLOCK | SG-COMP-217 | demo_lake_events:v2->v3:tags:element_type_change | tags | tables.demo_lake_events.versions.v3.fields.tags.type | collection element type changed at tags |

## Migration steps (advisory only)

| # | Target | Action | Affected consumers | Prerequisites | Reversible | Status | Blocking reasons |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | v2 | Publish declared schema v2 for readers of v1 | bi_reader, ml_reader | input fingerprint pinned | yes | blocked | bi_reader: backward mode blocks a new required field at ingest_source [SG-COMP-101; demo_lake_events:v1->v2:ingest_source:add_field]; bi_reader: backward mode blocks field removal at debug_flag [SG-COMP-103; demo_lake_events:v1->v2:debug_flag:drop_field]; ml_reader: full mode blocks change at debug_flag [SG-COMP-302; demo_lake_events:v1->v2:debug_flag:drop_field]; ml_reader: full mode blocks change at ingest_source [SG-COMP-302; demo_lake_events:v1->v2:ingest_source:add_field] |
| 2 | v2 | Enable writer cutover to v2 | etl_writer | step 1 completed | yes | blocked | etl_writer: forward mode blocks a new required field at ingest_source [SG-COMP-201; demo_lake_events:v1->v2:ingest_source:add_field]; etl_writer: forward mode blocks field removal at debug_flag [SG-COMP-203; demo_lake_events:v1->v2:debug_flag:drop_field] |
| 3 | v3 | Publish declared schema v3 for readers of v2 | bi_reader, ml_reader | step 2 completed | yes | ready | - |
| 4 | v3 | Enable writer cutover to v3 | etl_writer, ml_reader | step 3 completed | yes | blocked | ml_reader: full mode blocks change at payload.device [SG-COMP-302; demo_lake_events:v2->v3:payload.device:drop_field]; ml_reader: full mode blocks change at tags [SG-COMP-302; demo_lake_events:v2->v3:tags:element_type_change] |
| 5 | v3 | Retire compatibility declarations for older versions after sign-off | bi_reader, etl_writer, ml_reader | step 4 completed | no | blocked | bi_reader: backward mode blocks a new required field at ingest_source [SG-COMP-101; demo_lake_events:v1->v2:ingest_source:add_field]; bi_reader: backward mode blocks field removal at debug_flag [SG-COMP-103; demo_lake_events:v1->v2:debug_flag:drop_field]; etl_writer: forward mode blocks a new required field at ingest_source [SG-COMP-201; demo_lake_events:v1->v2:ingest_source:add_field]; etl_writer: forward mode blocks field removal at debug_flag [SG-COMP-203; demo_lake_events:v1->v2:debug_flag:drop_field]; ml_reader: full mode blocks change at debug_flag [SG-COMP-302; demo_lake_events:v1->v2:debug_flag:drop_field]; ml_reader: full mode blocks change at ingest_source [SG-COMP-302; demo_lake_events:v1->v2:ingest_source:add_field]; ml_reader: full mode blocks change at payload.device [SG-COMP-302; demo_lake_events:v2->v3:payload.device:drop_field]; ml_reader: full mode blocks change at tags [SG-COMP-302; demo_lake_events:v2->v3:tags:element_type_change] |

## Rollback hints

| For step | Pair | Roll back to | Retain fields | Consumers to roll back first | Note |
| --- | --- | --- | --- | --- | --- |
| 2 | v1->v2 | v1 | debug_flag, legacy_code, source_code | bi_reader, etl_writer, ml_reader | Keep old and new field names side by side until consumers move; this tool emits no DDL and executes no rollback. |
| 4 | v2->v3 | v2 | payload.device | ml_reader | Keep old and new field names side by side until consumers move; this tool emits no DDL and executes no rollback. |
| 5 | all | v3 | debug_flag, legacy_code, payload.device, source_code | bi_reader, etl_writer, ml_reader | Re-enable the prior compatibility declarations before retiring them; no external catalog or migration command is invoked. |

## Policy

- Policy ID: `default`
- Default mode: `backward`
- Forbidden changes: -
- Review changes: `decimal_change`, `field_order_change`, `rename_field`, `struct_field_change`

## Scope notice

SchemaGate is a local, offline, declarative admission check. It reads only the case fixtures, executes no DDL, opens no data files, and contacts no catalog or network service.
