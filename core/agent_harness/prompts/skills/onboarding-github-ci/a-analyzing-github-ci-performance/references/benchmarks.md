# CI/CD benchmark snapshot

Use these supplied values in the report's comparison columns. They are
frozen product benchmarks, labelled as measured on **2026-09-11** over
**30 days**, not live measurements taken during the user's analysis.

| Metric | langchain-ai/langchain | anomalyco/opencode |
|---|---:|---:|
| Red time on main | 7.5% | 31.6% |
| Mean time to green | 9.0h | 4.3h |
| CI-caused failure rate | 1.9% | 1.6% |
| Slowest normal run | 4m | 20m |
| PR failure rate | 11.5% | 54.6% |

Snapshot provenance: the `BENCHMARKS`, `MEASURED_ON`, and `WINDOW_DAYS`
data shipped in `integrations/github/tools/ci_analytics/benchmarks.py` on
2026-09-11. This reference includes the values so the skill can use them
without importing or running the report implementation. The original raw
peer records and coverage details are not bundled with this snapshot.

## Interpretation

Compare the normalized forms defined in `metrics.md`: red share, recovery
hours, same-SHA recovery rate, slowest workflow baseline, and PR run failure
rate. Put the target repository first. Raw execution counts, affected
authors, and working hours depend on repository and team size and have no
peer comparison here.

Caption the table with the benchmark date and window. Treat these figures
as historical context, not a controlled ranking: workloads differ, and
the old measurements' boundary and attempt handling cannot be verified
from this snapshot. If the target uses another reporting window or lacks
coverage for a metric, disclose that beside the comparison. A target
coverage gap does not change the supplied peer values.

Use the snapshot once; collecting fresh peer histories is separate work
for a user who explicitly requests a refreshed comparison.
