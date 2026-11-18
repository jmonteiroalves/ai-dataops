# Anomaly detection

Status: approved for R0. Scope: statistical detection over execution and quality metrics.
The engine never decides whether a data quality rule passed — that is deterministic and
belongs to the [quality engine](quality-strategy.md).

---

## 1. Principles

1. **Explainable first.** Every anomaly stores observed value, expected value, bounds,
   method, method version, sample size and the window used. A reviewer can recompute it.
2. **No ML in v1.** Robust statistics are sufficient for the demo volumes and are easier to
   defend in an interview than an unexplained model. ML is revisited only with evidence
   that these methods fail (see § 11).
3. **Seasonality is not optional.** Sales volume on Monday is not comparable to Sunday.
4. **Missing data is not zero.** A failed run produces no metrics; the engine must not read
   that as a volume collapse (the failure itself is the signal).
5. **Detection is not alerting.** An anomaly becomes an incident only through correlation,
   which applies persistence, impact and grouping.

---

## 2. Signal taxonomy

| `signal_type` | Produced by | Example |
|---|---|---|
| `operational_failure` | instrumentation | execution `failed`, timeout, stale run |
| `rule_violation` | quality engine | null rate above threshold, FK break |
| `schema_change` | schema check | column removed, type narrowed |
| `statistical_anomaly` | this engine | volume 29% below weekday baseline |

Only the last one is created here; the others are inputs to correlation.

---

## 3. Detectors

| # | Detector | Metric | Method | Minimum history | Notes |
|---|---|---|---|---|---|
| D1 | `robust_z_weekday` | `records_processed`, `row_count` | Tiered: full modified z-score with 8 same-weekday points; weekday median with a ratio gate for 3–7; rolling median below that | 3–8 same-weekday points | Ratio gate prevents false positives during warm-up |
| D2 | `robust_z_duration` | `duration_seconds` | Modified z-score on `log1p(duration)`, successful runs only | 14 | Right-skewed; log transform stabilizes variance |
| D3 | `rate_shift` | `null_rate.*`, `duplicate_rate`, `rejection_rate` | Two-proportion z-test + minimum absolute effect | 7 days or 50k rows | Significance without effect size is rejected |
| D4 | `psi_drift` | numeric columns (`mean`, `median`, deciles) | Population Stability Index vs baseline deciles | 30 days | Detects distribution change that averages hide |
| D5 | `freshness_lag` | `freshness_lag_minutes` | Rule SLA (deterministic) + robust z on lag | 14 | SLA breach is a rule violation, not a statistic |
| D6 | `failure_streak` | consecutive failed executions | Deterministic streak + binomial tail guard | 10 runs | Streak ≥ 2 escalates severity |
| D7 | `schema_diff` | column set/types | Set difference; breaking vs additive | none | Deterministic, no baseline |

Not implemented in v1 (documented, deliberately): change-point detection (CUSUM/Bayesian),
Prophet/STL decomposition, isolation forests, matrix-profile motifs. Each adds dependency or
tuning cost without a demonstrated gain on the demo dataset.

---

## 4. Methods and formulas

### 4.1 Modified z-score (robust)

```
median = median(x_1..x_n)
MAD    = median(|x_i − median|)
z      = 0.6745 · (x − median) / MAD        (MAD > 0)
```

`0.6745` makes the score comparable to a standard z-score under normality. Thresholds start
at `|z| ≥ 3.5` (Iglewicz–Hoaglin), which is deliberately conservative.

If `MAD = 0` (more than half the baseline identical — common for low-volume counters), fall
back to the IQR rule: flag if `x` is outside `[Q1 − 1.5·IQR, Q3 + 1.5·IQR]`, and mark
`confidence = 0.5`.

### 4.2 Weekday baseline (D1)

For a run on weekday `w`, pick the tier by available comparable history:

1. **8+ same-weekday points** — full tier. Median and MAD over those points, modified
   z-score, bounds at `median ± 3.5 × scale`. `method = robust_z_weekday`.
2. **3–7 same-weekday points** — short weekday tier. Median over those points; a signal is
   emitted only when `observed / median` is outside `[0.70, 1.30]`. Bounds are widened to
   ±20% and the score is the ratio itself. `method = weekday_median_short`.
3. **Fewer than 3 same-weekday points** — rolling tier. Median of the last 7 observations;
   emit only outside `[0.60, 1.60]`. `method = rolling_median_short`.

The ratio gates are the warm-up protection: a 4% noise band cannot trip a 30% gate, while an
injected 45% drop does. The tier is stored in `explanation` so the decision is auditable.
Stale runs (`data_date != logical_date`) skip volume detection entirely: the freshness rule
owns that condition, and comparing a Friday partition against a Sunday baseline would only
create a misleading signal.

### 4.3 Two-proportion z-test with an effect floor (D3)

```
p1 = failures_today / n_today          p2 = failures_baseline / n_baseline
p  = (failures_today + failures_baseline) / (n_today + n_baseline)
SE = sqrt(p(1−p)(1/n_today + 1/n_baseline))
z  = (p1 − p2) / SE
```

A shift is emitted only if **both** hold:

- `|z| ≥ 3` (statistical significance), and
- `|p1 − p2| ≥` absolute floor for the metric (1.0 pp for null/duplicate rates, 2.0 pp for
  rejection rate).

Without the floor, a 0.40% → 0.65% change on 200k rows is "significant" and operationally
irrelevant — the classic large-`n` false positive.

Minimum denominator: 500 rows. Below that, the detector returns `insufficient_data`.

### 4.4 Population Stability Index (D4)

Baseline deciles come from the last 30 days of the column; today's values are binned with the
same edges:

```
PSI = Σ_i (A_i − E_i) · ln(A_i / E_i)
```

`A_i` = share of today's rows in bin `i`, `E_i` = baseline share. Empty bins are replaced by
`1e-6` to keep the log finite. Interpretation used here: `< 0.10` stable, `0.10–0.25` moderate
shift, `> 0.25` major shift. PSI is a magnitude measure, not a significance test; it is
reported alongside the median shift.

### 4.5 Freshness (D5)

- Deterministic: `lag_minutes > sla_minutes` → `rule_violation` (quality engine).
- Statistical: `lag` is compared to its own history with the robust z-score; a *pattern* of
  growing lag (3 consecutive increases) is reported as `medium` even below the SLA.

### 4.6 Schema (D7)

Compare the captured schema with the last known contract:

| Change | Classification | Severity |
|---|---|---|
| Column added (nullable, no default required) | additive | `low` |
| Column added (required) | additive-breaking | `medium` |
| Column removed / renamed | breaking | `high` |
| Type narrowed (`STRING`→`INT64`, `NUMERIC`→`INT64`) | breaking | `high` |
| Type widened | additive | `low` |
| Nullability changed to `REQUIRED` | breaking | `medium` |

No baseline sample size is needed; this is set comparison, not statistics.

---

## 5. History tiers and warm-up

| Comparable history | Behaviour | Rationale |
|---|---|---|
| `< 3` same weekday and `< 5` recent | Detector disabled; records `insufficient_data` | Any threshold is arbitrary |
| `< 3` same weekday | Rolling median with a 0.60–1.60 ratio gate | No weekday signal yet; only large breaks are actionable |
| `3–7` same weekday | Weekday median with a 0.70–1.30 ratio gate, widened bounds | Directional baseline, not enough for MAD |
| `≥ 8` same weekday | Full weekday median/MAD robust z | 8 weeks per weekday |

The demo backfills **90 days**, so D1 and D3 start with full history and D4 with 30 days.
Warm-up days are excluded from the backtest metrics rather than counted as failures.

---

## 6. Severity

Severity is computed in two steps: **magnitude band**, then **modifiers**.

### Magnitude bands

| Signal | low | medium | high | critical |
|---|---|---|---|---|
| Robust z (D1, D2) | 3.5–4.5 | 4.5–6.0 | 6.0–8.0 | > 8.0 |
| Volume ratio to median | 0.85–0.90 | 0.70–0.85 | 0.50–0.70 | < 0.50 |
| Rate shift (pp) | 1–2 | 2–3 | 3–10 | > 10 |
| PSI | 0.10–0.15 | 0.15–0.25 | 0.25–0.40 | > 0.40 |
| Duration ratio | 1.5–2.0 | 2.0–3.0 | 3.0–5.0 | > 5.0 |
| Failure streak | 1 | 2 | 3 | ≥ 4 |
| Schema | additive | additive-breaking | breaking | — |

When two bands disagree (e.g. z says `high`, ratio says `medium`), take the **lower** band for
z-based detectors and the **higher** for explicit ratio rules; this asymmetry avoids
over-alerting on noisy z-scores while preserving obvious structural breaks.

### Modifiers

1. `+1` level if the dataset/pipeline `criticality = tier_1`.
2. `+1` level if the signal persists for ≥ 2 consecutive runs (hysteresis).
3. `−1` level if `criticality = tier_3`.
4. Cap at `critical`; floor at `info`.
5. `confidence` is reported separately and never used to change severity, so a low-confidence
   critical event is still visible but labelled.

Example: volume −29% (`medium` by z and by ratio) on a `tier_1` pipeline → `high`; if it
persists a second day → `critical`.

---

## 7. Violation vs anomaly

| | Rule violation | Statistical anomaly |
|---|---|---|
| Question | "Did the data break an explicit contract?" | "Is this behaviour unusual for this entity?" |
| Input | Rule config + current data | Baseline history + current value |
| Result | Binary, deterministic | Score + bounds + confidence |
| Needs history | No | Yes (tiered) |
| Severity source | Rule `severity` | Magnitude + modifiers |
| Repeatability | Identical for identical data | Depends on window; window is stored |
| Typical example | `order_id IS NULL` | Volume 29% below Tuesday median |

Both feed correlation. Neither overrides the other: a rule can fail while all metrics look
normal (e.g. a format regression), and a metric can drift while every rule passes (e.g. a
gradual mix shift).

---

## 8. Correlation and incidents

**Goal:** one incident per plausible root event, not one per symptom.

Algorithm (`heuristic_v1`):

1. **Collect** open signals in the bucket `[T−6h, T+6h]` (signals are timestamped, not
   assumed to belong to the current run).
2. **Build candidate pairs** and score association:

| Evidence | Weight |
|---|---|
| Same execution (failure + its metrics/rule results) | 1.0 |
| Direct lineage edge (upstream dataset → downstream dataset) within 2h | 0.8 |
| Same dataset, different metrics, within 1h | 0.6 |
| Same pipeline, within 1h | 0.5 |
| Shared symptom template (§ below) | +0.3 |

3. **Cluster** with union-find over pairs with weight ≥ 0.5. Clusters below 2 signals do not
   become incidents unless the single signal is `critical`.
4. **Name and score** the incident: `signature = hash(sorted signal_types + primary_entity)`,
   `severity = max(linked severity)` then `+1` if ≥ 3 linked signals (breadth indicates
   impact), capped at `critical`.
5. **Rank hypotheses** using templates, never asserting causality:

| Observed pattern | Candidate explanations (ordered, with evidence) |
|---|---|
| failure → volume drop → freshness → integrity | Upstream extract failure; downstream truncation; scheduler skip |
| volume drop + PSI + no failure | Source filter change; partial file; upstream segmentation |
| volume drop alone | Partial upstream load; source volume reduction; extract filter |
| null spike + schema change | Column renamed/removed; upstream contract break |
| duplicate spike + volume up | Replay without idempotency; join fan-out |
| duration spike + volume normal | Resource contention; query plan change; lock |

6. **Persist** `incidents` + `incident_events` with the evidence for each link (time delta,
   lineage path, shared key). Signals are never deleted or moved.
7. **Dedup**: `incident_id = UUIDv5(signature + 6h bucket)`. A new signal in the same bucket
   attaches to the existing incident; the next bucket creates a new one. This prevents both
   duplicate incidents and a single immortal incident.

**What the system must not say:** "the root cause is X". It says "the evidence is consistent
with X (confidence 0.7); verify by …". The LLM later receives the ranked hypotheses as input,
not as truth, and must keep the same framing.

---

## 9. False positive control and evaluation

### Controls

| Control | Effect |
|---|---|
| MAD-based z (not mean/std) | Outlier-resistant baseline |
| Weekday baseline | Removes weekly seasonality |
| Absolute effect floors | Rejects statistically significant but irrelevant shifts |
| Minimum sample size | Avoids noise on tiny partitions |
| Hysteresis (2 consecutive runs) | Filters one-off blips for `high`+ |
| Cooldown per entity+metric (24h) | Prevents alert floods; new evidence attaches to the incident |
| Multiple-comparison budget | At most N anomalies per run (default 20); extras are stored as `suppressed` with reason |
| Missing-data guard | Failed runs produce no metric anomalies |
| Human labels (`feedback_labels`) | Precision measured per detector, thresholds tuned from data |

### Backtest protocol

1. Generate a labelled history (90 days for the full demo; 45 for a quick run) with 24
   injected incidents, three per family. The scenario id is stored with each execution, so
   labels are explicit rather than inferred.
2. Replay the detector over the history in chronological order (no look-ahead).
3. Label each emission automatically from the scenario ground truth.
4. Report per detector and severity band:

| Metric | Definition | Target (v1) |
|---|---|---|
| Recall | injected incidents detected / injected incidents | ≥ 0.80 (measured: 1.00, 24/24) |
| Precision | detected days that were anomalous / detected days | ≥ 0.85 (measured: 1.00) |
| Detection delay | runs between injection and first emission | ≤ 1 run (measured: 0) |
| False positives | emissions on normal days | ≤ 1 per 30 normal days per detector |
| Expected-change suppression | known benign events not alerted | ≥ 2 of 3 |

Results are published in the README with the exact command to reproduce them. Warm-up days
are excluded and the exclusion is stated.

---

## 10. Worked example

**Signal:** Tuesday volume.

Baseline (last 8 Tuesdays): 20,100; 18,400; 21,300; 19,750; 20,800; 19,050; 20,600; 19,900.

- median = 20,000
- absolute deviations: 1,600; 950; 250; 100; 100; 600; 800; 1,300 → MAD = 700
- observed today = 14,200
- `z = 0.6745 × (14,200 − 20,000) / 700 = −5.59`
- ratio to the 7-day median (19,700) = 0.72

Interpretation: `|z|` in 4.5–6 → `medium`; ratio in 0.70–0.85 → `medium`; pipeline is
`tier_1` → `+1` → **high**. Persistence on the next run would make it `critical`.

**Companion signal:** null rate in `customer_id` moved from 0.40% (140k rows, 7 days) to
6.20% (14.2k rows).

- `z ≈ 68`, but the effect is 5.8 pp → far above the 1 pp floor.
- 6.2% of 14,200 ≈ 880 nulls vs an expected ~57.
- Severity by pp band (3–10) → `high`; `tier_1` → **critical**.

Correlation: same execution, same dataset, within one hour → same incident. The incident
shows both signals, ranks "upstream partial load / filter change" as the leading hypothesis,
and sends this bundle to diagnosis. It does **not** claim that the volume drop caused the
nulls.

---

## 11. Limits and when to reconsider ML

| Limit | Consequence | Mitigation |
|---|---|---|
| Short history | No detection for new entities | Tiered warm-up, explicit `insufficient_data` |
| Regime changes (pricing, promotion, season) | False positives | Expected-change flags, weekday baseline, feedback labels |
| Many metrics × entities | Multiple-comparison inflation | Per-run emission budget, persistence requirement |
| Small denominators | Unstable rates | Minimum sample size |
| Failed upstream means no metrics | Blind spot | Operational failure is a first-class signal, not a metric |
| Time zones / DST | Wrong "day" grouping | `logical_date` in `America/Sao_Paulo`, timestamps UTC |

ML (STL + residual models, isolation forest) is reconsidered only if the backtest shows
recall < 0.7 for a metric that matters. The decision would be recorded as an ADR with the
before/after benchmark, not adopted because it looks impressive.
