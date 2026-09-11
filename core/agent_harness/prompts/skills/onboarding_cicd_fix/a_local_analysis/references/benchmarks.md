# Comparing a repository's CI/CD metrics to public benchmarks

Phase 2 of the analytics demo (metric definitions in `metrics.md`): the
analyze call already paints one comparison table next to
`langchain-ai/langchain` and `anomalyco/opencode`. Load this reference only
when the user asks what a compared figure means.

## Rules

- The host table is the source. Peer columns are figures shipped with the
  product, and the table names the day they were measured. Do not call
  `analyze_github_ci_reliability` again for a benchmark, and never quote a
  figure from memory, a blog post, or a previous session.
- A coverage notice in the result concerns the user's repository only, not
  the peer columns.
- Compare **rates and durations**, never raw counts. A repository with 40 000
  runs a month and one with 400 are not comparable on `executions`,
  `pr_failures`, or `blocked_minutes`; they are comparable on
  `pr_failure_rate`, flake share, red share, `mean_recovery_hours`, and
  `normal_minutes`.
- The user's repository is the first column; the benchmarks are context.

## Default benchmarks

The tool always uses these two:

| Repository | Why it works as a benchmark |
|------------|-----------------------------|
| `langchain-ai/langchain` | Python monorepo, about ten Actions workflows, a few thousand runs a month |
| `anomalyco/opencode` | TypeScript monorepo, more workflows and several times the run volume; the high-throughput contrast |

## Comparable forms

`metrics.md` defines each figure and names its payload field; do not restate
them here. Only the normalisation a cross-repository comparison needs:

- `red_hours` → share of the window, `red_hours / (days × 24)`, as a percentage.
- `mean_recovery_hours` → as returned (already hours per outage).
- `reliability_failures` → share of `pr_executions`, as a percentage.
- `normal_minutes` → the slowest workflow in the table, not a median across
  workflows.
- `pr_failure_rate` → as returned.

`blocked_working_minutes`, `developers_affected`, and the per-developer list
depend on team size and working hours and are **not** compared across
repositories.
