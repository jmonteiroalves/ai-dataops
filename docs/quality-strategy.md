# Quality strategy

Status: approved for R0. Scope: rule-based validation of the demo datasets, with historical
results. Detection of *statistical* anomalies is a separate engine (see
[anomaly-detection.md](anomaly-detection.md)).

---

## 1. What "data quality" means here

Seven dimensions, each with an operational definition. Definitions are stored with the rule
so that a "completeness score" always means the same thing across datasets.

| Dimension | Question | Metric produced |
|---|---|---|
| **Completeness** | Are required values present? | `1 − null_count / evaluated` per column |
| **Uniqueness** | Are business keys unique? | `1 − duplicate_rows / evaluated` |
| **Validity** | Do values match the expected domain/format/range? | `conforming / evaluated` |
| **Consistency** | Are related fields internally coherent? | `coherent / evaluated` |
| **Integrity** | Do foreign keys resolve in the referenced dataset? | `resolved / evaluated` |
| **Freshness** | Is the data recent enough for its SLA? | `lag_minutes`, pass if `≤ sla` |
| **Schema** | Does the structure match the contract? | `pass` / `fail` + diff (breaking/additive) |

Rules are evaluated **per execution**, on the dataset version produced by that execution.
Results are immutable facts; aggregated scores are derived.

---

## 2. Tool boundary

Three tools can express data expectations. Using more than one for the same check creates
divergence, double maintenance and ambiguous ownership. The boundary is defined by the
question each tool answers:

| Question | Tool | When it runs | Output |
|---|---|---|---|
| "Is the transformation code correct against known fixtures?" | **dbt tests** (only if dbt is introduced) | build / CI | build failure |
| "Are today's data healthy against configurable business rules?" | **This engine** | every run | persisted results + history |
| "Do I want a large library of vendor-maintained expectations?" | Great Expectations (adapter, not adopted) | — | — |

**Decision for v1:** one rule catalog, executed by the custom engine. dbt is deferred: the
PNCP portfolio project already demonstrates dbt modeling and tests, so adding dbt here would
duplicate signal, add a second semantics for "test", and cost ~8–10 hours. Great Expectations
is evaluated and rejected as the primary engine for v1 (heavy dependency tree, SQLAlchemy
dependency, API churn between major versions, and it would own the same rules the engine
must own to produce historical scores). A thin `ExpectationExecutor` adapter is a documented
extension point, not a deliverable.

If dbt is adopted later, the rule is: **dbt tests assert model contracts in CI; the engine
asserts runtime data health.** A check never exists in both without a documented reason.

---

## 3. Rule catalog

Rules live in `rules/<dataset>/<rule_id>.yml`, are parsed into typed models, validated at
startup, and upserted into `dataops_meta.quality_rules` + `quality_rule_versions`.

```yaml
# rules/sales_clean/orders_completeness_customer_id.yml
rule_id: orders_completeness_customer_id
name: "customer_id must be present"
dataset: sales_clean
dimension: completeness
rule_type: not_null
columns: [customer_id]
severity: high
threshold: 0.99          # minimum acceptable pass rate
weight: 1.0
owner: data-platform
is_active: true
tags: [revenue, critical-path]
description: >
  customer_id is required for revenue attribution. Nulls here break the
  customer dimension join and understate daily revenue.
```

| Field | Required | Notes |
|---|---|---|
| `rule_id` | yes | stable natural key; never reused |
| `name` | yes | human-readable, appears in the dashboard |
| `dataset` | yes | must exist in `datasets` |
| `dimension` | yes | one of the seven |
| `rule_type` | yes | from the closed catalog (§ 4) |
| `columns` | depends | not used by `freshness`/`schema_contract` |
| `config` | depends | type-specific parameters |
| `severity` | yes | `info`/`low`/`medium`/`high`/`critical` |
| `threshold` | depends | meaning defined per type |
| `weight` | no (default 1.0) | used in the dataset score |
| `owner`, `tags`, `description` | no | governance and filtering |
| `is_active` | yes | deactivating keeps history |

**Anti-pattern avoided:** there is no free-form "expression" escape hatch by default. An
`expression` type exists but requires review and a `config_hash`; it is the only type that
can encode arbitrary SQL, and it must not contain DDL/DML (enforced by a statement whitelist
and a dry-run with `maximum_bytes_billed`).

---

## 4. Rule types (v1)

| `rule_type` | Dimension | Config | Pass condition | SQL shape |
|---|---|---|---|---|
| `not_null` | completeness | — | `null_rate ≤ 1 − threshold` | `COUNTIF(col IS NULL)` |
| `unique` | uniqueness | `columns` | `duplicate_rate ≤ 1 − threshold` | `GROUP BY key HAVING COUNT(*) > 1` |
| `accepted_values` | validity | `values[]`, `allow_null` | `conforming_rate ≥ threshold` | `COUNTIF(col IN (...))` |
| `regex_match` | validity | `pattern` | `conforming_rate ≥ threshold` | `REGEXP_CONTAINS` |
| `range` | validity | `min`, `max` | `conforming_rate ≥ threshold` | `COUNTIF(col BETWEEN ...)` |
| `expression` | consistency | `predicate` | `conforming_rate ≥ threshold` | `COUNTIF(NOT (predicate))` |
| `foreign_key` | integrity | `ref_dataset`, `ref_column` | `resolved_rate ≥ threshold` | `LEFT JOIN ... IS NULL` |
| `freshness` | freshness | `timestamp_column`, `sla_minutes` | `lag ≤ sla` | `MAX(ts)` vs clock |
| `schema_contract` | schema | `expected[]`, `mode` | ordered columns/types match (or additive-only) | `INFORMATION_SCHEMA.COLUMNS` |

Threshold semantics: `threshold` is the **minimum acceptable pass rate**, not the maximum
failure rate. `threshold: 1.0` means zero tolerance. A `warn` band can be configured as
`warn_threshold` (e.g. warn at 0.995, fail at 0.99).

---

## 5. Execution design

```
catalog (YAML)  ->  validator  ->  compiler  ->  executor  ->  result writer
                       |             |            |
                 fail fast on    one SQL per    per-rule isolation,
                 bad config      rule+dataset   timeout, bytes cap
```

1. **Load and validate** all active rules for the datasets touched by the execution. Invalid
   config aborts the *stage* with a clear error — never silently skips.
2. **Compile** each rule into a single `SELECT` returning
   `records_evaluated, records_passed, records_failed, observed_value`.
3. **Execute** rules in a bounded pool (default 4 concurrent queries), each with a statement
   timeout and `maximum_bytes_billed`.
4. **Classify** the result (`pass`/`warn`/`fail`/`error`/`skipped`) and persist one row.
5. **Aggregate** scores per dataset × dimension and write `quality_scores`.

Compiled example (`not_null`, threshold 0.99):

```sql
SELECT
  COUNT(*)                                   AS records_evaluated,
  COUNTIF(customer_id IS NOT NULL)           AS records_passed,
  COUNTIF(customer_id IS NULL)               AS records_failed,
  SAFE_DIVIDE(COUNTIF(customer_id IS NULL), COUNT(*)) AS observed_value
FROM `PROJECT.dataops_demo.sales_clean`
WHERE order_date = @logical_date
```

Notes:

- Queries always filter by the execution's logical date/partition. No full-table scans.
- `observed_value` is the metric's natural unit (null rate, duplicate rate, lag minutes),
  which makes cross-run comparison and later anomaly detection possible without re-parsing
  `details`.
- Empty datasets are **not** a pass. If `records_evaluated = 0`, the rule returns
  `insufficient_data` unless the rule type explicitly allows empty (e.g. a new partition on
  a non-SLA dataset).

### Status rules

| Status | When |
|---|---|
| `pass` | observed within the rule's pass condition |
| `warn` | between `warn_threshold` and `threshold` |
| `fail` | below `threshold` |
| `error` | SQL error, timeout, missing table/column |
| `skipped` | rule inactive for this run, or dataset not produced by this execution |
| `insufficient_data` | zero rows, or sample below `min_sample_size` |

`error` and `insufficient_data` never count as `pass` in the score.

---

## 6. Versioning

- A rule's identity (`rule_id`) is stable; its definition is immutable per version.
- Any change (threshold, column, severity, config) requires a new
  `quality_rule_versions` row with `change_reason`.
- `quality_rule_results` stores `rule_id` **and** `rule_version`, so historical comparisons
  never mix definitions.
- CI validates: no two active versions per rule, `config_hash` matches `config`, and
  `valid_to` is null only for the current version.
- Deleting a rule is a soft deactivate. Results remain queryable; the dashboard marks the
  rule as retired.

This is what allows the claim "the pass rate dropped" to be trustworthy: the definition did
not change underneath the chart.

---

## 7. Severity and alerting

Severity is configured per rule (business impact), not derived from the metric:

| Severity | Examples | Behaviour |
|---|---|---|
| `critical` | Nulls in `order_id`; FK break on revenue fact | Incident immediately, diagnosis candidate |
| `high` | Duplicate business keys, schema breaking change | Incident, diagnosis candidate |
| `medium` | Completeness below 99% on a non-key column | Incident, no AI by default |
| `low` | Format drift in a low-impact field | Recorded, grouped in daily digest |
| `info` | Observational check | Recorded only |

Rules do **not** page anyone. They create signals; the correlation engine decides whether a
signal becomes an incident (see anomaly-detection § 8). This avoids alert storms when one
root cause fails 12 rules at once.

---

## 8. Scores and history

- Rule pass rate = `records_passed / records_evaluated`.
- Dimension score = weighted mean of rule pass rates (`weight` from the catalog).
- Dataset score = weighted mean of dimension scores; dimensions with no valid results are
  omitted, and the UI shows "not evaluated" instead of a misleading 100%.
- Trend queries read `quality_scores` (one row per execution), never raw results, so the
  dashboard stays cheap.

Example output (demo, `sales_clean`, normal day):

| Dimension | Rules | Passed | Failed | Score |
|---|---|---|---|---|
| completeness | 4 | 4 | 0 | 1.000 |
| uniqueness | 1 | 1 | 0 | 1.000 |
| validity | 5 | 5 | 0 | 1.000 |
| consistency | 2 | 2 | 0 | 1.000 |
| integrity | 1 | 1 | 0 | 1.000 |
| freshness | 1 | 1 | 0 | 1.000 |
| schema | 1 | 1 | 0 | 1.000 |

With `null_spike`: completeness drops to 0.62 (2 of 4 rules fail), while the other dimensions
stay at 1.000 — a signal shape the anomaly engine can correlate with the volume signal.

---

## 9. Sampling, performance and cost

- Default: full-column aggregation in SQL (cheap on ≤ 1M rows, and exact).
- For large tables, `sampling: {method: system, percent: 10}` is allowed only with
  `min_sample_size` and is recorded in `details` so results are not compared unfairly.
- One query per rule, all partition-filtered; expected demo cost is a few MB per run.
- `maximum_bytes_billed` per rule query (200 MB) turns a bad predicate into an error, not a bill.
- Rule execution time is bounded per run; exceeding it marks remaining rules `skipped` with a
  reason, never silently.

---

## 10. Testing strategy

| Level | What | How |
|---|---|---|
| Unit — compiler | SQL shape per rule type | Snapshot tests on compiled SQL |
| Unit — evaluator | pass/warn/fail/error/insufficient_data | Table-driven fixtures |
| Unit — scoring | weights, missing dimensions, zero rows | Property-style cases |
| Integration | DuckDB end-to-end with a small orders fixture | pytest |
| Regression | Golden fixtures per dimension (nulls, duplicates, bad types, FK break, stale, schema change) | pytest markers |
| Contract | BigQuery adapter parity (same rule, same result) | optional, tagged `gcp` |

Minimum required cases: all-null column, empty table, duplicate composite key, type
mismatch, FK orphan, stale partition, dropped column, renamed column, extra column.

---

## 11. Extension path

Adding a rule type requires: (1) a typed config model, (2) a compiler function, (3) at least
one fixture, (4) a documentation row in § 4. Nothing else changes — the executor, scoring,
storage and dashboard are type-agnostic.

A Great Expectations adapter, if ever built, would implement the same `RuleExecutor` port:
catalog rule → `ExpectationConfiguration` → `Validator` → the same `RuleResult`. It would
not introduce a second catalog.
