# Synthetic RDS PostgreSQL Suite

This suite benchmarks RDS PostgreSQL root-cause analysis against bundled telemetry fixtures instead of live AWS infrastructure. Each scenario is a static evidence snapshot served through a `FixtureGrafanaBackend`, which drives the same agentic pipeline (`plan → investigate → diagnose`) used in production.

## Scenario table

| ID  | Name                              | Difficulty | True root cause     | Adversarial element                          | Forbidden                     |
| --- | --------------------------------- | ---------- | ------------------- | -------------------------------------------- | ----------------------------- |
| 000 | healthy                           | 1          | healthy             | none                                         | resource_exhaustion           |
| 001 | replication-lag                   | 1          | resource_exhaustion | none                                         | —                             |
| 002 | connection-exhaustion             | 1          | resource_exhaustion | none                                         | —                             |
| 003 | storage-full                      | 1          | resource_exhaustion | none                                         | —                             |
| 004 | cpu-saturation-bad-query          | 1          | resource_exhaustion | none                                         | —                             |
| 005 | failover                          | 1          | infrastructure      | none                                         | —                             |
| 006 | replication-lag-cpu-redherring    | 2          | resource_exhaustion | CPUUtilization elevated (analytics job)      | category: cpu_saturation      |
| 007 | connection-pressure-noisy-healthy | 2          | healthy             | CPU/connections oscillating near-threshold   | category: resource_exhaustion |
| 008 | storage-full-missing-metric       | 3          | resource_exhaustion | FreeStorageSpace absent from fixture         | —                             |
| 009 | dual-fault-connection-cpu         | 4          | resource_exhaustion | connections + CPU both failing, causally linked | keywords: storage, replication |
| 010 | replication-lag-missing-metric    | 3          | resource_exhaustion | ReplicaLag metric absent from fixture          | —                             |

## Difficulty levels

| Level | Description |
| ----- | ----------- |
| 1     | Single dominant signal — all evidence consistent, root cause identifiable in one step |
| 2     | One confounder present — second evidence source needed to rule it out |
| 3     | Absent or indirect evidence — key metric missing, agent must infer from what remains |
| 4     | Compositional fault — two failure modes active and causally linked |

## MECE basis

Uniqueness is on `(primary_signal × rate × corroborating_presence × event_presence)`, not on primary signal alone.

003 and 008 both map to `storage_full` but have distinct fingerprints:
- **003**: `FreeStorageSpace` present and trending to 0 with elevated `WriteIOPS`
- **008**: `FreeStorageSpace` absent from the fixture entirely — agent must infer from events + PI write latency

## Scoring

Each scenario passes when all of the following are true:

1. The model returns a non-empty root cause
2. The predicted `ROOT_CAUSE_CATEGORY` matches `answer.yml`
3. Every required keyword from `answer.yml:required_keywords` appears in the output
4. The actual category is not in `answer.yml:forbidden_categories` (level 2+ scenarios)
5. No forbidden keyword from `answer.yml:forbidden_keywords` appears in the output (level 4 scenario)
6. Every source listed in `answer.yml:required_evidence_sources` is non-empty in `final_state["evidence"]` — proves the agent consulted the right evidence, not just keyword-matched the alert title

## Each scenario folder contains

- `scenario.yml`: scenario metadata (engine, difficulty, adversarial_signals, depends_on)
- `alert.json`: synthetic alert payload
- `cloudwatch_metrics.json`: CloudWatch metric evidence (may omit metrics to simulate collection gaps)
- `rds_events.json`: RDS event stream for the incident window
- `performance_insights.json`: top SQL and wait-event evidence
- `answer.yml`: expected category, required keywords, optional forbidden constraints, required evidence sources

## Running

Via the interactive CLI (recommended):

```bash
opensre tests synthetic
```

Run the whole suite directly:

```bash
python -m tests.synthetic.rds_postgres.run_suite --mock-grafana
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

- **Levels 1–2** (scenarios 000–007): run on every commit
- **Levels 3–4** (scenarios 008–009): deferred to nightly — these require the agent to demonstrate indirect inference and causal reasoning that may be sensitive to LLM temperature

## Known gaps

- **Temporal ordering**: all scenarios deliver evidence as a static snapshot. Production delivers evidence incrementally (alert fires → query metrics → query events → …). Testing temporal ordering requires architectural changes to the fixture backend and is out of scope.
- **Level 4 coverage**: only one compositional fault scenario (009). A fuller curriculum would include 2–3 dual-fault combinations across different failure mode pairs.
- **Slack/markdown renderer for multi-fault**: the renderer displays a single `root_cause` string. Compositional faults may eventually need a `root_causes: list` field in the schema.

## Dependency: healthy_rca_state

Scenario 007 depends on `HEALTHY_SHORT_CIRCUIT=true` (the default) and the `healthy` category being wired into the LLM prompt. If you run with `HEALTHY_SHORT_CIRCUIT=false`, scenario 007 will fall through to the LLM path, which should still classify as `healthy` — but the test is most deterministic with the short-circuit enabled.
