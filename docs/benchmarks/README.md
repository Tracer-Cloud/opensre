# Benchmark

This benchmark runs a fixed subset of synthetic scenarios:
- 001-replication-lag
- 002-connection-exhaustion
- 003-storage-full

Reported metrics:
- duration
- token usage
- estimated LLM cost

Not reported:
- accuracy
- false positives
- false negatives

## Running benchmarks

From the repository root:

```shell
make benchmark
```

This runs the benchmark suite **and** updates the `## Benchmark` section in
`README.md` with a summary table. The full report is written to
`docs/benchmarks/results.md`.

To update only the README from a previously generated report (no LLM calls):

```shell
make benchmark-update-readme
```

To skip the README update during a benchmark run:

```shell
python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator --no-update-readme
```

## How the README auto-update works

The main `README.md` contains two HTML comment markers:

```html
<!-- BENCHMARK-START -->
...summary content...
<!-- BENCHMARK-END -->
```

After each benchmark run, the content between these markers is replaced with
the latest summary table. The replacement is idempotent — running benchmarks
multiple times replaces the previous results rather than appending duplicates.

This follows the same pattern used by the contributors workflow
(`.github/workflows/contributors.yml`).

A GitHub Actions workflow (`.github/workflows/benchmark-readme.yml`) also
runs automatically when `docs/benchmarks/results.md` changes on `main`,
keeping the README in sync without manual intervention.

## Output files

- `docs/benchmarks/results.md` — full per-case report with detailed metrics
- `README.md` (benchmark section) — compact summary table

## Custom README path

To write the summary to a different README file:

```shell
python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator --readme-path /path/to/README.md
```

## Running selected scenarios

```shell
python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator \
    --scenario 001-replication-lag \
    --scenario 002-connection-exhaustion
```
