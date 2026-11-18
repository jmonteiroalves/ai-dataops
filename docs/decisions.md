# Architecture decisions

Format: context → decision → alternatives considered → consequences. Status is `accepted`
unless stated otherwise. Decisions are revisited only with evidence, and a superseded ADR is
never deleted.

| ID | Decision | Status |
|---|---|---|
| [ADR-001](#adr-001--local-first-with-duckdb-as-the-default-engine) | Local-first with DuckDB as the default engine | Accepted |
| [ADR-002](#adr-002--single-cloud-run-job-with-subcommands-as-orchestrator) | Single Cloud Run Job with subcommands as orchestrator | Accepted |
| [ADR-003](#adr-003--custom-sql-rule-engine-dbt-deferred-great-expectations-not-primary) | Custom SQL rule engine; dbt deferred; GX not primary | Accepted |
| [ADR-004](#adr-004--unified-long-metric-observations-table) | Unified long `metric_observations` table | Accepted |
| [ADR-005](#adr-005--deterministic-ids-and-idempotent-merge) | Deterministic IDs and idempotent `MERGE` | Accepted |
| [ADR-006](#adr-006--logical-keys-because-bigquery-does-not-enforce-constraints) | Logical keys (BigQuery has no PK/FK) | Accepted |
| [ADR-007](#adr-007--public-dashboard-serving-a-pre-computed-snapshot) | Public dashboard serving a pre-computed snapshot | Accepted |
| [ADR-008](#adr-008--gemini-flash-lite-via-vertex-ai-with-adc) | Gemini Flash-Lite via Vertex AI with ADC | Accepted |
| [ADR-009](#adr-009--robust-statistics-only-no-machine-learning-in-v1) | Robust statistics only, no ML in v1 | Accepted |
| [ADR-010](#adr-010--evidence-grounded-structured-llm-output-with-no-tools) | Evidence-grounded structured LLM output, no tools | Accepted |
| [ADR-011](#adr-011--serving-views-snapshot-export-and-per-query-bytes-cap) | Serving views, snapshot export, per-query bytes cap | Accepted |
| [ADR-012](#adr-012--us-central1-as-the-demo-region) | `us-central1` as the demo region | Accepted |
| [ADR-013](#adr-013--synthetic-data-with-ground-truth-labels) | Synthetic data with ground-truth labels | Accepted |
| [ADR-014](#adr-014--implement-statistics-in-python-without-scipy) | Implement statistics in Python without scipy | Accepted |
| [ADR-015](#adr-015--heuristic-correlation-never-asserted-causality) | Heuristic correlation, never asserted causality | Accepted |
| [ADR-016](#adr-016--python-312-uv-and-hatchling) | Python 3.12, `uv`, hatchling | Accepted |
| [ADR-017](#adr-017--correlation-candidates-guided-by-the-llm) | Correlation candidates guided by the LLM | Accepted |
| [ADR-018](#adr-018--terraform-owns-infrastructure-ci-rolls-images) | Terraform owns infrastructure; CI rolls images | Accepted |

---

## ADR-001 — Local-first with DuckDB as the default engine

**Date:** 2026-11-17

**Context.** The project must be cheap, testable in CI without credentials, and runnable by a
reviewer who will not create a GCP project just to evaluate a portfolio piece. BigQuery's
free tier is generous but requires billing setup and network access.

**Decision.** Define a narrow warehouse port (execute query, load batch, table metadata) and
provide two adapters: DuckDB/Parquet (default) and BigQuery. Business logic depends only on
the port. The Docker image for GCP is the same code with `DATAOPS_ENGINE=bigquery`.

**Alternatives.**
- *BigQuery only*: simplest, but every test needs credentials and the demo cannot run offline.
- *SQLite*: weaker analytical SQL (window functions are fine, but arrays/JSON/PSI are awkward).
- *Postgres in Docker*: heavier, still not the target engine.

**Consequences.** (+) Offline demo, fast CI, cost-free development, adapter parity is a real
test target. (−) SQL must stay within the intersection of DuckDB and BigQuery dialects;
a parity suite is required, and dialect-specific functions are isolated in small macros.

---

## ADR-002 — Single Cloud Run Job with subcommands as orchestrator

**Date:** 2026-11-17

**Context.** The flow has seven stages that must run in order, be retryable and be safe
against concurrent runs. Orchestration options range from a Python CLI to Cloud Composer.

**Decision.** One container image, one Cloud Run Job, one CLI with subcommands:
`run-pipeline`, `run-quality`, `detect`, `correlate`, `diagnose`, `export-serving`,
`run-daily` (the sequence). Cloud Scheduler invokes `run-daily`. `parallelism=1`,
`max_retries=1`, plus a logical lock row in BigQuery.

**Alternatives.**
- *Cloud Composer*: ~US$ 300+/month floor and a second DSL; unjustifiable here.
- *Cloud Workflows*: adds a YAML DSL, a second deployment unit, and is harder to run locally.
- *Eventarc/Pub/Sub chain*: more moving parts, harder idempotency reasoning for a batch flow.

**Consequences.** (+) Local `dataops run-daily` is exactly what runs in the cloud; one image,
one log stream, trivial cost. (−) No visual DAG; retries are per-stage rather than per-task;
the CLI must enforce ordering explicitly (it does, and tests assert it).

---

## ADR-003 — Custom SQL rule engine; dbt deferred; Great Expectations not primary

**Date:** 2026-11-17

**Context.** The brief lists dbt Core and Great Expectations. The platform must produce
historical rule results and scores — functionality that overlaps both tools. Using all three
would create three definitions of "test".

**Decision.** The custom engine owns runtime data quality: a versioned YAML catalog compiled
to SQL, with persisted results and scores. dbt is deferred (the PNCP project already
demonstrates dbt modeling and tests, so it adds little portfolio signal here). Great
Expectations is evaluated and not adopted as the primary engine; a `RuleExecutor` adapter is
a documented extension point.

**Alternatives.**
- *GX as the engine*: heavy dependency tree (pandas, SQLAlchemy), breaking API changes between
  major versions, and its suites would still need a separate history/score layer.
- *dbt tests only*: excellent for model contracts in CI, but they run at build time, do not
  produce historical pass rates per rule, and cannot express runtime freshness or volume.
- *GX + dbt + custom*: maximum overlap, maximum maintenance.

**Consequences.** (+) One catalog, one semantics, explainable SQL, small dependency surface.
(−) No vendor ecosystem or prebuilt expectation library; the README must state this trade-off
openly rather than imply GX is used.

---

## ADR-004 — Unified long `metric_observations` table

**Date:** 2026-11-17

**Context.** Metrics differ in unit and entity: row counts, durations, null rates per column,
PSI per column, freshness lag. A table per metric would multiply schemas, queries and
detector code.

**Decision.** One table: `(entity_type, entity_id, metric_name, value, unit, observed_at,
execution_id, dimensions)`. Typed serving views expose wide shapes for the dashboard.

**Alternatives.**
- *Wide table per entity*: adding a metric is a schema migration.
- *One table per metric family*: simpler types, but detectors and dashboards need unions.

**Consequences.** (+) One detector interface, one history query, easy to add metrics.
(−) `metric_name` must be a controlled vocabulary, and unit mismatches are possible; both are
enforced by validation and tests.

---

## ADR-005 — Deterministic IDs and idempotent `MERGE`

**Date:** 2026-11-17

**Context.** Batch jobs retry, backfills replay dates, and the scheduler can fire twice. Any
random identifier creates duplicates that corrupt history and detector baselines.

**Decision.** Every fact row has a UUIDv5 derived from its natural key
(e.g. `pipeline_id:logical_date:attempt`). Writes use `MERGE` on that key. Terminal states are
never overwritten by non-terminal ones.

**Alternatives.**
- *Auto-increment / `GENERATE_UUID()`*: duplicates on retry.
- *Deduplicate downstream*: hides the problem and makes counts untrustworthy.

**Consequences.** (+) Replays are safe, backfills are safe, tests assert zero duplicates.
(−) Natural keys must be stable and documented; changing one is a breaking change.

---

## ADR-006 — Logical keys because BigQuery does not enforce constraints

**Date:** 2026-11-17

**Context.** BigQuery does not enforce primary or foreign keys. Referential integrity is a
documentation and application concern.

**Decision.** Keys are logical. Integrity is enforced by (1) a single write path per table,
(2) `MERGE` on deterministic keys, (3) uniqueness and relationship assertion queries run in
CI and daily, and (4) operational failure of the assertion creating a platform incident.

**Alternatives.**
- *Assume the warehouse enforces it*: wrong and dangerous.
- *Move to a relational database*: loses the serverless/columnar benefits that the project is
  meant to demonstrate.

**Consequences.** (+) Honest architecture; a common interview question is answered in the
docs. (−) Extra tests and a runtime assertion job.

---

## ADR-007 — Public dashboard serving a pre-computed snapshot

**Date:** 2026-11-17 (supersedes the earlier private-proxy idea)

**Context.** The dashboard must be reachable from the public portfolio. A public Streamlit app
that queries BigQuery per request exposes an unbounded cost surface (anonymous traffic ×
query bytes) and would require giving a public service account data access.

**Decision.** The daily job exports serving views to a versioned JSON snapshot in Cloud
Storage (JSON locally; Parquet remains an option for the BigQuery export in R3). The Cloud Run service reads the snapshot at startup, caches it, and never queries the
warehouse. It runs with `--max-instances=2`, no write permissions, and no BigQuery role.
The snapshot is also committed (or attached as a release asset) so the dashboard runs with
zero GCP.

**Alternatives.**
- *Private Cloud Run + `gcloud run services proxy`*: safe but not accessible to recruiters.
- *IAP + external HTTPS load balancer*: ~US$ 18/month and configuration overhead.
- *Public app querying BigQuery*: rejected on cost and security.
- *Static HTML export only*: cheapest, but loses the investigation UX that the project is about.

**Consequences.** (+) Public, cheap, read-only by construction, works offline. (−) Data is as
fresh as the last export (daily); no ad-hoc queries from the UI; the export step is a critical
path that needs monitoring.

---

## ADR-008 — Gemini Flash-Lite via Vertex AI with ADC

**Date:** 2026-11-17

**Context.** Diagnosis needs a model that follows a JSON schema, is cheap per call, and does
not require a long-lived credential in the repository. Options: Gemini API (AI Studio key) or
Vertex AI (IAM/ADC).

**Decision.** Default model `gemini-3.5-flash-lite` (pinned by ID and version per diagnosis),
called through Vertex AI using Application Default Credentials in location `global`.
Model ID and prices live in `llm_models`. The project used for the measured run is
`ai-dataops-demo-jma`. The AI Studio path is supported only for local experiments via an
optional key.

**Alternatives.**
- *AI Studio API key*: simpler, but a static secret in a container; free tier allows training
  on data, which is unacceptable even for synthetic content in a deployed demo.
- *Larger model (3.8 Flash / 4.x)*: better reasoning, 2–3x cost; unnecessary for a structured
  interpretation task with a small evidence bundle.

**Consequences.** (+) No keys, auditable IAM, cost predictable (~US$ 0.004/diagnosis).
(−) Vertex model availability must be verified per region; prices may differ slightly from the
Gemini API page; model upgrades require a new prompt version and a golden-set replay.

---

## ADR-009 — Robust statistics only, no machine learning in v1

**Date:** 2026-11-17

**Context.** Anomaly detection could use STL/Prophet, isolation forests or autoencoders. The
demo has 90 days of data per entity and a small number of metrics.

**Decision.** Use median/MAD z-scores, weekday baselines, two-proportion tests with effect
floors, PSI, IQR fallback and deterministic schema/streak checks.

**Alternatives.** ML models would need more history, tuning and explainability work, and would
make the interview story weaker ("the model said so").

**Consequences.** (+) Every alert is reproducible by hand; low cost; fast. (−) Weaker on
complex multivariate patterns; documented as a limitation, with a stated trigger to revisit
(backtest recall < 0.70 on a metric that matters).

---

## ADR-010 — Evidence-grounded structured LLM output with no tools

**Date:** 2026-11-17

**Context.** LLMs produce plausible narratives and are vulnerable to prompt injection from
monitored content. The platform must not become an uncontrolled operator.

**Decision.** The model receives a compact, canonical evidence bundle, returns JSON validated
against a schema, must cite `evidence_id` values that exist in the bundle, must separate facts
from hypotheses, and has no tools, no SQL and no ability to act. Invalid output is retried
once, then stored as `invalid`.

**Alternatives.**
- *Free-text summaries*: unverifiable and hard to evaluate.
- *Agent with SQL tools*: attractive demo, unacceptable risk and cost; explicitly out of scope.
- *Fine-tuning*: not the capability being demonstrated.

**Consequences.** (+) Auditable diagnoses, measurable groundedness, safe default.
(−) Less fluent than free-form output; the prompt and schema become versioned artifacts that
must be maintained.

---

## ADR-011 — Serving views, snapshot export and per-query bytes cap

**Date:** 2026-11-17

**Context.** BigQuery charges by bytes scanned. Dashboards and exploratory queries are the
classic source of surprise bills.

**Decision.** The dashboard reads only exported snapshots. Analytical queries are
partition-filtered, and every client sets `maximum_bytes_billed` (200 MB in the demo). Large
tables require a partition filter.

**Alternatives.** BI Engine reservation (cost floor), materialized views (refresh cost and
staleness), caching only in the app (does not protect the warehouse from other clients).

**Consequences.** (+) Bounded worst case, simple mental model. (−) Freshness is bounded by the
export schedule; ad-hoc analysis requires a human with BigQuery access.

---

## ADR-012 — `us-central1` as the demo region

**Date:** 2026-11-17

**Context.** The author is in Brazil. São Paulo (`southamerica-east1`) reduces latency but is
a Tier 2 Cloud Run region and is priced higher; the portfolio audience is global.

**Decision.** BigQuery dataset location and Cloud Run resources in `us-central1`. Business
dates use `America/Sao_Paulo`, timestamps are UTC.

**Alternatives.** `southamerica-east1` (latency, data residency optics) at a slightly higher
cost and with model-availability checks.

**Consequences.** (+) Maximum free-tier adherence, lowest price, one region for compute and
warehouse (no cross-region egress). (−) Higher latency from Brazil (~120–180 ms), irrelevant
for a batch dashboard.

---

## ADR-013 — Synthetic data with ground-truth labels

**Date:** 2026-11-17

**Context.** A detector demo without labels can only show charts. The project claims precision
and recall; that requires knowing which days were anomalous.

**Decision.** A seeded generator produces 90 days of history and injects labelled scenarios.
Labels are stored with the execution (`scenario_id`) and used by the backtest to compute
precision/recall. Benign "expected changes" are included as negative controls.

**Alternatives.** Public datasets (no ground truth for injected faults), real production data
(unavailable and not shareable).

**Consequences.** (+) Measurable claims, reproducible demos, regression tests for detectors.
(−) Synthetic distributions are simpler than reality; results are indicative, and the README
says so.

---

## ADR-014 — Implement statistics in Python without scipy

**Date:** 2026-11-17

**Context.** The needed statistics are median, MAD, IQR, proportions and PSI. scipy brings a
large binary dependency into the container for functions that are a few lines each.

**Decision.** Implement the formulas directly with NumPy/pandas and unit-test them against
known values. No scipy in v1.

**Alternatives.** scipy for `norm.sf`/`ks_2samp`. Revisit only if a test requires a
distribution function that is error-prone to implement.

**Consequences.** (+) Smaller image, faster cold start, formulas visible in code (good for the
portfolio narrative). (−) Must test the math carefully, including edge cases (zero MAD,
empty bins, tiny samples).

---

## ADR-015 — Heuristic correlation, never asserted causality

**Date:** 2026-11-17

**Context.** A single upstream failure produces many symptoms. Presenting each as an
independent incident is noise; asserting a root cause is unsupported.

**Decision.** Correlate signals within a 6-hour bucket using execution identity, lineage,
entity and symptom templates. Produce one incident with ranked hypotheses and the evidence
for each link. Language is probabilistic ("consistent with"), and the LLM receives the
hypotheses as input, not as truth.

**Alternatives.** No correlation (noisy), causal inference (no data or identification
strategy), LLM-only correlation (non-deterministic and unevaluable).

**Consequences.** (+) Fewer, better incidents; honest uncertainty. (−) Heuristic weights need
tuning; a missed lineage edge splits an incident (mitigated by manual lineage review).

---

## ADR-016 — Python 3.12, `uv`, hatchling

**Date:** 2026-11-17

**Context.** The environment must be reproducible locally, in CI and in the container, with a
lockfile and a fast install.

**Decision.** Python 3.12, `uv` for dependency resolution and virtualenvs, `hatchling` as the
build backend, `pyproject.toml` as the single source of configuration for Ruff, mypy and pytest.

**Alternatives.** pip + requirements files (no lock, slower), Poetry (heavier, slower
resolver), Conda (not needed).

**Consequences.** (+) Fast, reproducible, one tool for env and lock. (−) Contributors need
`uv` installed; the Makefile falls back to `python -m venv` + pip if `uv` is unavailable.


---

## ADR-017 — Correlation candidates guided by the LLM

**Date:** 2026-11-18

**Context.** The diagnosis prompt initially received only raw signals. With one anomaly and
no error logs, the model often refused (78% of incidents) or invented a generic cause. The
deterministic correlation layer already ranks candidate explanations from signal types and
lineage; not passing them to the model discarded useful, explainable context.

**Decision.** The evidence bundle includes `candidate_explanations` produced by correlation
(cause, confidence, verification steps). The model must accept, reject or refine them and
cite evidence. The evaluation measures strict category accuracy and a synonym-tolerant
variant, and the report states that the score is candidate-assisted.

**Alternatives.**
- *Raw signals only*: more refusals and lower category agreement, as measured in R2 v1/v2.
- *Let the LLM do correlation*: non-deterministic, unevaluable, and it would blur the
  deterministic/AI boundary that the project is built to demonstrate.

**Consequences.** (+) Fewer refusals (78% → 0%), higher category agreement, consistent
terminology between layers. (−) The model can be anchored to a wrong candidate; the prompt
requires explicit rejection, and the incident detail shows candidates separately from the
diagnosis so a reviewer can judge it.


---

## ADR-018 — Terraform owns infrastructure; CI rolls images

**Date:** 2026-11-18

**Context.** The cloud demo needs BigQuery, Cloud Run, Scheduler, IAM, a public dashboard
and CI/CD. Applying Terraform from CI requires a remote state bucket, broad IAM on the deploy
identity and careful handling of the billing account, which is more operational surface than
a portfolio demo needs.

**Decision.** Terraform owns the infrastructure and is applied manually by the author after
review (`plan` → approval → `apply`). GitHub Actions validates the configuration on every
change (`fmt`, `init -backend=false`, `validate`) and, on manual dispatch, builds both images
and rolls them into the existing Cloud Run Job/Service via `gcloud run ... update`. The deploy
identity is federated through Workload Identity Federation; there are no service account keys.

**Alternatives.**
- *Terraform apply from CI*: requires a state bucket, billing permissions and a larger blast
  radius; unnecessary for a single-operator demo.
- *gcloud-only deployment*: loses reproducible infrastructure and the reviewable plan.

**Consequences.** (+) Reproducible infrastructure with a reviewed plan; CI cannot accidentally
recreate or destroy cloud resources; no long-lived credentials. (−) Two tools own different
layers (Terraform: infrastructure; gcloud: image rollout), documented in the runbook. A future
production version would move apply into CI with a protected environment and remote state.

---

## ADR-019 — BigQuery uses MERGE and batch load jobs

**Date:** 2026-11-18

**Context.** The DuckDB adapter relies on `INSERT OR REPLACE` and multi-row `INSERT`. BigQuery
has no enforced keys and bills streaming inserts, while batch load jobs are free.

**Decision.** The BigQuery adapter implements `write_rows` as a parameterised `MERGE` on the
logical key (chunked to stay under parameter limits), and `append_rows` as JSON load jobs with
`WRITE_APPEND`. Partitioned demo tables are deleted per logical date before loading. DDL is
generated from the same table specification as DuckDB, with BigQuery types and
partition/cluster options.

**Alternatives.**
- *Streaming inserts*: simpler but billed and unnecessary for daily batches.
- *A separate schema per engine*: risks drift; one specification generates both DDLs and the
  key/type maps used by MERGE.

**Consequences.** (+) Free batch ingestion, idempotent metadata writes, one schema definition.
(−) MERGE has a parameter ceiling, hence 100-row chunks; the cloud adapter is unit-tested but
awaits a live smoke test when the stack is applied.
