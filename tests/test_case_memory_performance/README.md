# Memory Performance Test Case

Tests and benchmarks for the memory system speedup capabilities.

## Overview

This test case validates that the memory system achieves the 50% wall-clock speedup target for RCA investigations.

## Test Files

### test_memory_speed.py
E2E speed test that validates 50% speedup requirement.

**Usage:**
```bash
pytest tests/test_case_memory_performance/test_memory_speed.py -v -s
```

**What it tests:**
- Baseline run (no memory, Claude Sonnet)
- Memory run (with memory, Claude Haiku + guidance)
- Asserts: memory_time <= 0.5 * baseline_time

**Dependencies:**
- Requires Prefect ECS infrastructure deployed
- Uses test_case_upstream_prefect_ecs_fargate as target scenario
- Requires ANTHROPIC_API_KEY

### benchmark_memory.py
Comprehensive benchmark with multiple iterations for statistical analysis.

**Usage:**
```bash
python3 tests/test_case_memory_performance/benchmark_memory.py
```

**What it does:**
- Runs 3 baseline iterations (no memory)
- Runs 3 memory iterations (with memory)
- Computes mean, standard deviation, variance
- Validates speedup consistency
- Reports quality metrics

## Results

See [`app/memories/BENCHMARK_RESULTS.md`](../../app/memories/BENCHMARK_RESULTS.md) for detailed analysis.

### Current Status

**Haiku approach (implemented):**
- Mean speedup: 35.1%
- High variance: ±11.45s
- Status: ❌ Below 50% threshold, unreliable

**Needed for 50% target:**
- Aggressive caching (skip LLM calls on exact match)
- Expected: 87% speedup (consistent)
- Trade-off: Only works on repeat investigations

## Target Scenario

Uses the **upstream_downstream_pipeline_prefect** test case:
- External API schema change
- Lambda ingestion
- S3 landing + audit trail
- Prefect flow validation failure
- Full upstream tracing

This is a realistic, complex investigation that exercises all RCA capabilities.

## Running Tests

**Quick validation:**
```bash
pytest tests/test_case_memory_performance/test_memory_speed.py -v -s
```

**Comprehensive benchmark:**
```bash
python3 tests/test_case_memory_performance/benchmark_memory.py > benchmark_results.txt
```

## Documentation

- [`app/memories/IMPLEMENTATION_PLAN.md`](../../app/memories/IMPLEMENTATION_PLAN.md) - Original 6-milestone plan
- [`app/memories/FINDINGS.md`](../../app/memories/FINDINGS.md) - Why adding context failed
- [`app/memories/BENCHMARK_RESULTS.md`](../../app/memories/BENCHMARK_RESULTS.md) - Statistical analysis
- [`app/memories/SUCCESS.md`](../../app/memories/SUCCESS.md) - Achievement documentation (outdated - see BENCHMARK_RESULTS.md)
