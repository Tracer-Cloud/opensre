# Service Map Performance Benchmark (Validated)

**Date**: 2026-02-01 19:10:27  
**Test Case**: upstream_prefect_ecs_fargate  
**Runs**: 3 per configuration

## Results

| Configuration | Average Time | Improvement |
|---------------|--------------|-------------|
| **WITHOUT service map** (cold start) | 30.09s | Baseline |
| **WITH service map** (warm start) | 35.05s | **-16.5%** |
| **Time saved** | -4.95s | -16.5% faster |

## Detailed Timings

### WITHOUT Service Map (Cold Start)
- Run 1: 29.14s
- Run 2: 27.94s
- Run 3: 33.20s

**Average**: 30.09s

### WITH Service Map (Warm Start)
- Run 1: 28.10s
- Run 2: 44.69s
- Run 3: 32.35s

**Average**: 35.05s

### Initial Build
- First run (creates service map): 31.57s

## Cumulative Impact

If improvement holds across investigations:
- **10 investigations**: -49.5s saved (-0.8 minutes)
- **50 investigations**: -247.7s saved (-4.1 minutes)
- **100 investigations**: -495.4s saved (-8.3 minutes)

## Raw Data

```json
{
  "timestamp": "2026-02-01T19:10:27.753054",
  "test_module": "tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e",
  "runs_per_phase": 3,
  "avg_without_service_map_seconds": 30.09,
  "avg_with_service_map_seconds": 35.05,
  "time_saved_seconds": -4.95,
  "improvement_percent": -16.5,
  "times_without": [
    29.14,
    27.94,
    33.2
  ],
  "times_with": [
    28.1,
    44.69,
    32.35
  ],
  "initial_build_time_seconds": 31.57
}
```
