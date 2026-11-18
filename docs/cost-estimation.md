# Cost estimation

Status: planning estimate, not a bill. Prices checked on **2026-11-17** at the URLs listed in
§ 8. Re-verify before deploying: GCP pricing and Gemini model prices change, and one of the
candidate models has an announced increase on 2027-01-01.

---

## 1. Design constraints that drive cost

| Constraint | Effect |
|---|---|
| Serverless only, scale to zero | No idle compute |
| Batch loads, never streaming inserts | Load jobs are free |
| Partitioned tables + partitioning/clustering | Queries scan MB, not history |
| Dashboard serves a pre-computed snapshot | Public traffic never runs BigQuery |
| LLM only for high-severity, deduplicated incidents | ~100 calls/month |
| 30–400 day retention | Storage stays under 1 GiB |
| No min-instances, no Composer, no BI Engine | No fixed monthly floor |

---

## 2. Assumptions

| Parameter | Value | Basis |
|---|---|---|
| Environment | 1 GCP project, `us-central1` | Decision |
| Demo runs | 1 scheduled + ~1 manual/day → 60/month | Demo cadence |
| Job size | 1 vCPU, 1 GiB, ~2 min per run | Estimated |
| Data volume | ~450k orders (90 days × 5k/day), < 500 MB | Synthetic |
| Queries | ~30 GB scanned/month | ~20 rule queries × 60 runs + exports |
| Dashboard sessions | 300–500/month (portfolio); 5,000/month stress case | Public, read-only |
| Snapshot size | ~5 MB Parquet total | Aggregated serving views |
| Diagnoses | 100/month | High/critical incidents only |
| Tokens per diagnosis | 3,000 input / 1,200 output | Budget in [ai-diagnosis](ai-diagnosis.md) |
| Logs | < 1 GiB/month | Structured, no row payloads |
| Billing account | Shared with the PNCP portfolio project | Free tiers are per account |

---

## 3. Free tier used

| Service | Free tier (per billing account/month) | Demo usage | Covered |
|---|---|---|---|
| BigQuery queries (on-demand) | 1 TiB | ~30 GB | Yes |
| BigQuery storage | 10 GiB | < 1 GiB | Yes |
| BigQuery batch load | Free | 60 loads | Yes |
| BigQuery Storage Write API (gRPC) | 2 TiB | not used | n/a |
| Cloud Run Jobs | 240,000 vCPU-s + 450,000 GiB-s | ~7,200 + 7,200 | Yes |
| Cloud Run services (request-based) | 180,000 vCPU-s + 360,000 GiB-s + 2M requests | ~1,000 vCPU-s | Yes |
| Cloud Scheduler | 3 jobs | 1–2 jobs | Yes |
| Cloud Logging | 50 GiB | < 1 GiB | Yes |
| Cloud Build | 120 build-minutes/day | ~100 min/month | Yes |
| Artifact Registry | 0.5 GiB | ~1 GiB | **No** (partial) |
| Vertex AI Gemini | No free tier on Vertex | 100 calls | **No** |
| Network egress (North America) | 1 GiB/month | ~0.75 GB | Yes (borderline at higher traffic) |

> Free tiers are measured **per billing account**, not per project. If the PNCP project also
> uses BigQuery or Cloud Run in the same account, both consume the same allowance. At these
> volumes the combined usage is still far below the limits, but the aggregate should be
> checked in **Billing → Reports** rather than assumed.

---

## 4. Unit prices used

| Item | Price | Source |
|---|---|---|
| BigQuery on-demand query | US$ 6.25 / TiB (first 1 TiB free) | BigQuery pricing |
| BigQuery active logical storage | ~US$ 0.023 / GiB-month (first 10 GiB free) | BigQuery pricing |
| Cloud Run Jobs CPU | US$ 0.000018 / vCPU-s | Cloud Run pricing |
| Cloud Run Jobs memory | US$ 0.000002 / GiB-s | Cloud Run pricing |
| Cloud Run service CPU (request-based) | US$ 0.000024 / vCPU-s | Cloud Run pricing |
| Cloud Run service memory | US$ 0.0000025 / GiB-s | Cloud Run pricing |
| Cloud Run requests | US$ 0.40 / 1M | Cloud Run pricing |
| Cloud Scheduler | US$ 0.10 / job-month (3 free) | Scheduler pricing |
| Artifact Registry | ~US$ 0.10 / GiB-month (0.5 GiB free) | Artifact Registry pricing |
| Internet egress (North America, premium) | ~US$ 0.12 / GiB (1 GiB free) | Cloud Run / network pricing |
| Gemini 3.5 Flash-Lite input | US$ 0.30 / 1M tokens | Gemini API pricing (Vertex location `global`) |
| Gemini 3.5 Flash-Lite output | US$ 2.50 / 1M tokens | Gemini API pricing |

Vertex AI list prices may differ slightly from the Gemini API table; confirm in the Vertex AI
pricing page before the R3 budget review.

---

## 5. Monthly estimate

### 5.1 Demo environment (portfolio public dashboard)

| Line item | Calculation | Cost |
|---|---|---|
| BigQuery queries | 30 GB × US$ 6.25/TiB = US$ 0.18, inside 1 TiB free | US$ 0.00 |
| BigQuery storage | 0.5 GiB, inside 10 GiB free | US$ 0.00 |
| Cloud Run Job | 7,200 vCPU-s and 7,200 GiB-s, inside free tier | US$ 0.00 |
| Cloud Run dashboard | ~1,000 vCPU-s, 5,000 requests, inside free tier | US$ 0.00 |
| Scheduler | 1 job, inside 3 free | US$ 0.00 |
| Artifact Registry | ~1 GiB − 0.5 GiB free = 0.5 GiB × 0.10 | US$ 0.05 |
| Logging | < 1 GiB, inside 50 GiB free | US$ 0.00 |
| Egress | 300 sessions × ~1.5 MB ≈ 0.45 GB, inside 1 GiB free | US$ 0.00 |
| Gemini Flash-Lite | 100 × ~US$ 0.003 (measured) | US$ 0.30 |
| **Total** | | **≈ US$ 0.36** |

Round up for pricing drift and a few manual backfills: **US$ 0.40–0.90/month**.

Measured in R2: 24 diagnoses cost **US$ 0.073** (US$ 0.003 each, prompt v3, including
retries). Total experimentation spend during development was **US$ 0.62**, still well below
the US$ 2 monthly application budget.

### 5.2 Stress case (dashboard goes viral, 5,000 sessions)

| Line item | Calculation | Cost |
|---|---|---|
| Cloud Run dashboard | ~10,000 vCPU-s (inside free) + 7.5 GB egress | ~US$ 0.78 |
| BigQuery | still fixed (snapshot), no change | US$ 0.00 |
| Gemini | unchanged (incidents, not page views) | US$ 0.39 |
| **Total** | | **≈ US$ 1.25** |

This is the key property of the snapshot design: **page views do not scale BigQuery cost**.

### 5.3 Scale scenarios for the AI line

| Incidents/month | Gemini cost | Note |
|---|---|---|
| 20 | US$ 0.08 | One demo week |
| 100 | US$ 0.39 | Baseline |
| 500 | US$ 1.95 | Heavier demo |
| 2,000 | US$ 7.80 | Would require a real production workload |

If a larger model is used (e.g. Gemini 3.8 Flash at US$ 0.75 / 3.75 per 1M, list price until
2026-12-31), the per-diagnosis cost is ~US$ 0.007 and the 100-incident line becomes ~US$ 0.70.
That model's list price **doubles on 2027-01-01**; the default remains Flash-Lite.

---

## 6. Guardrails

| Risk | Control | Where |
|---|---|---|
| Runaway query cost | `maximum_bytes_billed` = 200 MB per query | BigQuery client config |
| Accidental full scans | partitioning/clustering on large tables | Terraform schema |
| Daily query abuse | Custom quota: bytes processed per day | Project quota |
| Traffic spike on dashboard | `--max-instances=2`, `--concurrency=10`, 300 s timeout | Cloud Run |
| Dashboard DB access | Dashboard SA has no BigQuery role | IAM |
| LLM loop | Evidence hash dedup, 6h cooldown, monthly budget, `skipped_budget` | Diagnosis service |
| Log volume | No row payloads, 30-day expiration, severity filter on export | Logging + schema |
| Image accumulation | Keep last 3 image tags; lifecycle policy on the repository | Artifact Registry |
| Silent spend | Budget alerts at US$ 1, US$ 5, US$ 10 (50/80/100%) | Cloud Billing |
| Zombie resources | `terraform destroy` documented and rehearsed | Runbook (R3) |

Additional cheap safety nets: label every resource `project=ai-dataops` and `env=demo`, so
Billing reports can be filtered; enable `--cpu-throttling` on the dashboard; disable the
Scheduler job when the demo is not being shown.

---

## 7. What would make this expensive (anti-patterns)

| Anti-pattern | Why it hurts | Avoided by |
|---|---|---|
| Streaming inserts per row | Billed per volume, wake-ups | Batch load + `MERGE` |
| Dashboard querying raw tables per request | Cost scales with visitors | Snapshot |
| `SELECT *` without partition filter | Scans all history | Partition filter + bytes cap |
| Min-instances = 1 | 730 h/month of idle CPU | Scale to zero |
| Cloud Composer | ~US$ 300+/month floor | Cloud Run Jobs |
| Sending all events to Gemini | Linear LLM cost, no dedup | Severity gate + evidence hash |
| Retrying diagnosis on failure without limit | Multiplies cost during an outage | Single retry + circuit breaker |
| Full logs in BigQuery | Storage + query growth | Truncation + expiration |
| Public BigQuery access from a browser | Unbounded scan by anonymous users | No client-side BQ access |

---

## 8. Sources and verification

| Topic | URL |
|---|---|
| BigQuery pricing (queries, storage, ingestion) | https://cloud.google.com/bigquery/pricing |
| Cloud Run pricing (services, jobs, free tier) | https://cloud.google.com/run/pricing |
| Cloud Scheduler pricing (3 free jobs) | https://cloud.google.com/scheduler/pricing |
| Artifact Registry pricing (0.5 GiB free) | https://cloud.google.com/artifact-registry/pricing |
| Gemini API pricing | https://ai.google.dev/gemini-api/docs/pricing |
| Vertex AI generative AI pricing | https://cloud.google.com/vertex-ai/generative-ai/pricing |
| GCP free program (US$ 300 / 90 days) | https://cloud.google.com/free |

The US$ 300 credit for new accounts covers this project for well beyond the build period, but
the budget should be planned as if it did not exist.

Status note: the Terraform stack is deployed (2026-11-18). The cloud demo holds 33 days of
history, 13 incidents and 13 diagnoses; measured Gemini spend for the deployed demo is
**US$ 0.042** (~US$ 0.003 per diagnosis). Infrastructure cost remains within the projections
above; confirm against the first billing export.

---

## 9. Teardown

```bash
# after exporting the final snapshot used by the public demo
terraform -chdir=infra/terraform destroy
gcloud storage rm --recursive gs://<snapshot-bucket>   # includes object versions
```

Expected residual cost after teardown: US$ 0.00. Retain in the repository only the exported
snapshot and screenshots needed for the portfolio, never credentials or Terraform state.
