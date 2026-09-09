# Comparing a repository's CI/CD metrics to public benchmarks

Phase 2 of the analytics demo (metric definitions in `metrics.md`): the
analyze call with `include_benchmarks=true` already paints one comparison
table next to apache/airflow and fastapi/fastapi over the same window. Load
this reference only when the user asks what a compared figure means.

## Rules

- The host table is the source. Peers come from today's snapshot only; a
  miss is skipped, not fetched live. Do not call
  `analyze_github_ci_reliability` again for a benchmark, and never quote a
  figure from memory, a blog post, or a previous session.
- Compare **rates and durations**, never raw counts. A repository with 40 000
  runs a month and one with 400 are not comparable on `executions`,
  `pr_failures`, or `blocked_minutes`; they are comparable on
  `pr_failure_rate`, flake share, red share, `mean_recovery_hours`, and
  `normal_minutes`.
- The user's repository is the first column; the benchmarks are context.
- If the tool returns `benchmarks_skipped` or `coverage_notices` for a
  peer, say so in one line. Do not substitute another repository without
  telling the user.

## Default benchmarks

The tool always uses these two when `include_benchmarks` is true:

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
