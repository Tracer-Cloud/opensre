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

Run from repository root:

make benchmark

Alternative direct command:

python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator

Output file:
- docs/benchmarks/results.md

Optional: run only selected scenarios:

python -m tests.benchmarks.toolcall_model_benchmark.benchmark_generator --scenario 001-replication-lag --scenario 002-connection-exhaustion