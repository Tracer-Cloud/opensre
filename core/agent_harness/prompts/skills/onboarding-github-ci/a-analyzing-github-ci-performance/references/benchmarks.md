# Comparing a repository's CI/CD metrics to public benchmarks

Phase 2 of the analytics demo (metric definitions in `metrics.md`): the
analyze call already paints one comparison table next to apache/airflow and
fastapi/fastapi. Load
this reference only when the user asks what a compared figure means.

## Rules

- The host table is the source. Peer columns are figures shipped with the
  product (the report names the day they were measured). Do not call
  `analyze_github_ci_reliability` again for a benchmark, and never quote a
  figure from memory, a blog post, or a previous session.
- Compare **rates and durations**, never raw counts. A repository with 40 000
  runs a month and one with 400 are not comparable on `executions`,
  `pr_failures`, or `blocked_minutes`; they are comparable on
  `pr_failure_rate`, flake share, red share, `mean_recovery_hours`, and
  `normal_minutes`.
- The user's repository is the first column; the benchmarks are context.
- The benchmark figures ship with OpenSRE and the table names the day they
  were measured; a coverage notice in the result concerns the user's
  repository only. Never quote a benchmark figure from memory.

## Default benchmarks

The tool always uses these two:

| Repository | Why it works as a benchmark |
|------------|-----------------------------|
| `apache/airflow` | Python monorepo, dozens of Actions workflows, high PR volume |
| `fastapi/fastapi` | Mid-size Python project with a lean Actions setup |

Do not use `kubernetes/kubernetes` as a stand-in. Its CI runs on Prow, and
the repository keeps only a handful of GitHub Actions workflows, so the
numbers would say nothing about Kubernetes' actual CI health.

## Metric mapping

Each metric in `metrics.md` maps to fields the tool already returns.

| Metric (`metrics.md`) | Field(s) | Comparable form |
|-----------------------|----------|-----------------|
| #1 Red time on main | `red_hours`, window `days` | `red_hours / (days × 24)` as a percentage |
| #2 Mean time to green | `mean_recovery_hours`, `outages` | hours per outage |
| #3 Flake rate | `reliability_failures`, `pr_executions` | `reliability_failures / pr_executions` as a percentage |
| #4 Normal CI duration | `workflows[].normal_minutes` | slowest `normal_minutes` in the table (not a median across workflows) |
| PR failure rate | `pr_failure_rate` | as returned |

`blocked_working_minutes`, `developers_affected`, and the per-developer list
depend on team size and working hours and are **not** compared across
repositories.
