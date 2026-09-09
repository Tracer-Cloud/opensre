# Comparing a repository's CI/CD metrics to public benchmarks

Phase 2 of the analytics demo (see `insights.md`): after the user's own
numbers are on screen, put them next to one or two well-known open-source
repositories so the user can tell "is 6 % flake rate bad?" without prior
experience. Load this reference only when the user asks how they compare, or
when the analysis is done and a comparison is the natural next question.

## Rules

- Benchmark numbers come from `analyze_github_ci_reliability`, called on the
  benchmark repository with the **same `days`** as the user's analysis. Never
  quote a benchmark figure from memory, a blog post, or a previous session;
  public repositories change week to week and the tool is the only source the
  reply may cite.
- Compare **rates and durations**, never raw counts. A repository with 40 000
  runs a month and one with 400 are not comparable on `executions`,
  `pr_failures`, or `blocked_minutes`; they are comparable on
  `pr_failure_rate`, flake share, red share, `mean_recovery_hours`, and
  `normal_minutes`.
- Keep the user's repository first and the benchmarks after it; the benchmark
  is context for the user's number, not the headline.
- If the tool returns `coverage_notices` for a benchmark, say so in one line
  next to that benchmark row. Do not drop the row silently and do not
  substitute another repository without telling the user.
- A benchmark call that fails on a missing token or rate limit ends the
  comparison for that repository; report the user's own numbers unchanged and
  say which benchmark could not be fetched.

## Picking benchmarks

The tool reads GitHub Actions workflow runs, so a benchmark must run its CI on
Actions. Default to one benchmark of a similar size and one much larger
project the user will recognize:

| Repository | Why it works as a benchmark |
|------------|-----------------------------|
| `apache/airflow` | Python monorepo, dozens of Actions workflows, high PR volume, familiar to most teams |
| `fastapi/fastapi` | Mid-size Python project with a lean Actions setup; a realistic target for a small team |
| `pydantic/pydantic` | Library CI with a matrix build; good "normal duration" reference |
| `astral-sh/ruff` | Rust toolchain on Actions; fast, disciplined CI |
| `huggingface/transformers` | Large ML repo; heavy matrix, long-running jobs |
| `pytorch/pytorch` | Very large Actions fleet; upper bound for duration and outage counts |
| `grafana/grafana` | Large Go/TypeScript monorepo; good for teams that run both |

Do not use `kubernetes/kubernetes` as a benchmark for this tool. Its CI runs on
Prow (`prow.k8s.io`), and the repository keeps only a handful of GitHub
Actions workflows (Copilot, dependency graph), so the tool would report a
near-empty picture that says nothing about Kubernetes' actual CI health. If
the user asks for Kubernetes by name, say this in one sentence and offer
`grafana/grafana` or `pytorch/pytorch` as a large-project stand-in.

Let the user pick when they name a repository; otherwise take
`apache/airflow` plus the row above closest to the user's language
and repository size.

## Metric mapping

Each metric in `metrics.md` maps to fields the tool already returns. Compute
the derived shares in the reply only from those fields, and show the formula
once in the table caption so the user can reproduce it.

| Metric (`metrics.md`) | Field(s) | Comparable form |
|-----------------------|----------|-----------------|
| #1 Red time on main | `red_hours`, window `days` | `red_hours / (days × 24)` as a percentage |
| #2 Mean time to green | `mean_recovery_hours`, `outages` | hours per outage; note `outages` so a single long outage is not read as a trend |
| #3 Flake rate | `reliability_failures`, `pr_executions` | `reliability_failures / pr_executions` as a percentage |
| #4 Normal CI duration | `workflows[].normal_minutes` | median of `normal_minutes` across workflows with runs, in minutes |
| #5 Merged PRs delayed by a flake | `blocked_prs`, `merged_pr_branches` | `len(blocked_prs) / merged_pr_branches` as a percentage (the tool caps `blocked_prs` at 10; if it returns exactly 10, label the share "≥") |
| PR failure rate | `pr_failure_rate` | as returned |

`blocked_working_minutes`, `developers_affected`, and the per-developer list
depend on team size and working hours and are **not** compared across
repositories. Mention the user's own values for these only if the user asks;
the headline already covers the biggest one.

## Workflow

1. Confirm the user's analysis exists in this session (step 3 of the demo).
   If it does not, run it first; do not benchmark an unanalyzed repository.
2. Choose benchmarks per "Picking benchmarks". If the user did not name one,
   say which two you are using and why in one sentence.
3. Call `analyze_github_ci_reliability(owner=…, repo=…, days=<same window>)`
   once per benchmark. Do not re-run it for the user's repository.
4. Render one table, user first, using the "Comparable form" column above.
   Round percentages to one decimal and durations to whole minutes or one
   decimal hour. Put `coverage_notices` as a footnote line under the table.
5. Close with at most two sentences: the one metric where the user's
   repository differs most from the benchmarks, and what the demo can do about
   it (the recurring reliability agent from step 4 of the demo). No further
   recap.

## Reply shape

```
Compared with apache/airflow and fastapi/fastapi over the same 30 days:

| Metric                       | <owner/repo> | apache/airflow | fastapi/fastapi |
|------------------------------|-------------:|---------------:|----------------:|
| Red time on main             |        4.2 % |                  1.1 % |           0.3 % |
| Mean time to green (h)       |          6.5 |                    1.8 |             0.9 |
| Flake rate                   |        7.9 % |                  2.4 % |           0.6 % |
| Normal CI duration (median)  |       23 min |                 14 min |           9 min |
| Merged PRs delayed by flakes |       18.0 % |                  5.2 % |           1.4 % |
| PR failure rate              |       21.0 % |                 12.7 % |           4.9 % |

Red time = red_hours / (days × 24); flake rate = reliability_failures / pr_executions.
fastapi/fastapi: <coverage notice from the tool, if any>

Your flake rate is roughly three times Airflow's; the recurring reliability agent
targets exactly those same-commit fail-then-pass runs.
```

The figures above are placeholders showing layout only; every number in a
real reply must come from the tool calls made in this session.
