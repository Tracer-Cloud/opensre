# Synthetic RDS PostgreSQL Suite

This suite benchmarks RDS PostgreSQL root-cause analysis against bundled telemetry fixtures instead of live AWS infrastructure. Each scenario is a static evidence snapshot served through a `FixtureGrafanaBackend`, which drives the same agentic pipeline (`plan → investigate → diagnose`) used in production.

## Scenario table

### Axis 1 — Efficiency (marker: `synthetic`)

| ID  | Name                              | Difficulty | True root cause     | Adversarial element                             | Forbidden                     |
| --- | --------------------------------- | ---------- | ------------------- | ----------------------------------------------- | ----------------------------- |
| 000 | healthy                           | 1          | healthy             | none                                            | resource_exhaustion           |
| 001 | replication-lag                   | 1          | resource_exhaustion | none                                            | —                             |
| 002 | connection-exhaustion             | 1          | resource_exhaustion | none                                            | —                             |
| 003 | storage-full                      | 1          | resource_exhaustion | none                                            | —                             |
| 004 | cpu-saturation-bad-query          | 1          | resource_exhaustion | none                                            | —                             |
| 005 | failover                          | 1          | infrastructure      | none                                            | —                             |
| 006 | replication-lag-cpu-redherring    | 2          | resource_exhaustion | CPUUtilization elevated (analytics job)         | category: cpu_saturation      |
| 007 | connection-pressure-noisy-healthy | 2          | healthy             | CPU/connections oscillating near-threshold      | category: resource_exhaustion |
| 008 | storage-full-missing-metric       | 3          | resource_exhaustion | FreeStorageSpace absent from fixture            | —                             |
| 009 | dual-fault-connection-cpu         | 4          | resource_exhaustion | connections + CPU both failing, causally linked | keywords: storage, replication |
| 010 | replication-lag-missing-metric    | 3          | resource_exhaustion | ReplicaLag metric absent from fixture           | —                             |

### Axis 2 — Reasoning (marker: `axis2`)

Axis 2 scenarios use `SelectiveGrafanaBackend`. The agent must ask for the right metrics and explicitly rule out alternative hypotheses. Each scenario has `ruling_out_keywords` that must appear in the agent's output.

| ID  | Name                               | Difficulty | True root cause     | Red herring / adversarial element                                    | Must rule out                             |
| --- | ---------------------------------- | ---------- | ------------------- | -------------------------------------------------------------------- | ----------------------------------------- |
| 011 | cpu-storage-compositional          | 4          | resource_exhaustion | ReplicaLag elevation and connection spike as side effects            | connection_exhaustion, replication        |
| 012 | replication-lag-misleading-events  | 3          | resource_exhaustion | Three historical infra events in event stream (none are root cause)  | infrastructure (failover as root cause)   |
| 013 | storage-recovery-false-alert       | 3          | healthy             | FreeStorageSpace spike + WriteLatency brief elevation                | resource_exhaustion (already recovered)   |
| 014 | checkpoint-storm-cpu-saturation    | 4          | resource_exhaustion | CPU at 92% — alert fires on CPU but cause is VACUUM checkpoint storm | cpu_saturation (CPU is symptom, not root) |

## Difficulty levels

| Level | Description |
| ----- | ----------- |
| 1     | Single dominant signal — all evidence consistent, root cause identifiable in one step |
| 2     | One confounder present — second evidence source needed to rule it out |
| 3     | Absent or indirect evidence — key metric missing, or misleading signals require timeline reasoning |
| 4     | Compositional fault — two failure modes active; agent must identify both and correctly characterise causally-linked side effects |

## MECE basis

Uniqueness is on `(primary_signal × rate × corroborating_presence × event_presence)`, not on primary signal alone.

003 and 008 both map to `storage_full` but have distinct fingerprints:
- **003**: `FreeStorageSpace` present and trending to 0 with elevated `WriteIOPS`
- **008**: `FreeStorageSpace` absent from the fixture entirely — agent must infer from events + PI write latency

## Scoring

### Axis 1 (all scenarios — `test_suite.py`)

Each scenario passes when all of the following are true:

1. The model returns a non-empty root cause
2. The predicted `ROOT_CAUSE_CATEGORY` matches `answer.yml`
3. Every required keyword from `answer.yml:required_keywords` appears in the output
4. The actual category is not in `answer.yml:forbidden_categories` (level 2+ scenarios)
5. No forbidden keyword from `answer.yml:forbidden_keywords` appears in the output (level 4 scenario)
6. Every source listed in `answer.yml:required_evidence_sources` is non-empty in `final_state["evidence"]` — proves the agent consulted the right evidence, not just keyword-matched the alert title

Additionally, a `TrajectoryScore` is computed for each scenario with an `optimal_trajectory`:
- `sequencing_ok`: all expected action types appear in the agent's executed action log (set membership; parallel execution order is non-deterministic)
- `calibration_ok`: number of investigation loops ≤ `max_investigation_loops`
- `efficiency_score`: mean(sequencing_ok, calibration_ok); 1.0 = full pass

### Axis 2 (adversarial scenarios — `test_suite_axis2.py`, marker: `axis2`)

Axis 2 runs through `SelectiveGrafanaBackend`, which:
- Returns only the metric series matching the agent's `metric_name` query (case-insensitive substring)
- Records every metric name the agent requested in `queried_metrics` (audit log)

On top of all Axis 1 checks, Axis 2 asserts a `ReasoningScore`:
- `ruling_out_ok`: every token in `answer.yml:ruling_out_keywords` appears in agent output (proves the agent addressed and dismissed alternative hypotheses)
- `queries_ok`: every entry in `answer.yml:required_queries` was requested by the agent via `query_timeseries` — **not yet testable**: the current action registry hardcodes `metric_name="pipeline_runs_total"` for all `query_grafana_metrics` calls; `required_queries` is reserved for when the agent supports per-metric querying
- `reasoning_score`: mean(ruling_out_ok, queries_ok); 1.0 = full pass

### Gap metric

The gap between Axis 1 and Axis 2 pass rates is the primary health indicator for adversarial robustness. A large gap means the agent can find answers when handed all data but cannot reason when it must choose what to look at. Track it per difficulty level:

```bash
python -m tests.synthetic.rds_postgres.run_suite --mock-grafana --axis2
```

## Each scenario folder contains

- `scenario.yml`: scenario metadata (engine, difficulty, adversarial_signals, depends_on)
- `alert.json`: synthetic alert payload
- `aws_cloudwatch_metrics.json`: CloudWatch metric evidence (may omit metrics to simulate collection gaps)
- `aws_rds_events.json`: RDS event stream for the incident window
- `aws_performance_insights.json`: top SQL and wait-event evidence
- `answer.yml`: expected category, required keywords, optional forbidden constraints, required evidence sources

## Running

Via the interactive CLI (recommended):

```bash
opensre tests synthetic
```

Run the whole Axis 1 suite directly:

```bash
python -m tests.synthetic.rds_postgres.run_suite --mock-grafana
```

Run Axis 2 adversarial scenarios via pytest:

```bash
pytest -m axis2 tests/synthetic/rds_postgres/test_suite_axis2.py -v
```

Run a single scenario:

```bash
python -m tests.synthetic.rds_postgres.run_suite --scenario 006-replication-lag-cpu-redherring --mock-grafana
```

Print JSON results:

```bash
python -m tests.synthetic.rds_postgres.run_suite --mock-grafana --json
```

## CI tier strategy

- **Axis 1, Levels 1–2** (scenarios 000–007): run on every commit (`@synthetic`)
- **Axis 1, Levels 3–4** (scenarios 008–010): deferred to nightly — require indirect inference
- **Axis 2** (scenarios 011–013, `@axis2`): run nightly alongside Axis 1 levels 3–4; gap is reported each run

## Known gaps

- **Temporal ordering**: all scenarios deliver evidence as a static snapshot. Production delivers evidence incrementally (alert fires → query metrics → query events → …). Testing temporal ordering requires architectural changes to the fixture backend and is out of scope.
- **Level 4 coverage**: two compositional fault scenarios (009, 011). A fuller curriculum would include 3–4 dual-fault combinations across different failure mode pairs.
- **Slack/markdown renderer for multi-fault**: the renderer displays a single `root_cause` string. Compositional faults may eventually need a `root_causes: list` field in the schema.
- **Axis 2 `required_queries` not yet enforced**: The agent action registry hardcodes `metric_name="pipeline_runs_total"` for all `query_grafana_metrics` calls. `SelectiveGrafanaBackend` records all queried metric names as an audit log but does not filter results. When the agent is updated to pass dynamic CloudWatch metric names (e.g. `metric_name="CPUUtilization"`), re-enable filtering in `SelectiveGrafanaBackend.query_timeseries` and set `required_queries` in `answer.yml`.

## Dependency: healthy_rca_state

Scenario 007 depends on `HEALTHY_SHORT_CIRCUIT=true` (the default) and the `healthy` category being wired into the LLM prompt. If you run with `HEALTHY_SHORT_CIRCUIT=false`, scenario 007 will fall through to the LLM path, which should still classify as `healthy` — but the test is most deterministic with the short-circuit enabled.
