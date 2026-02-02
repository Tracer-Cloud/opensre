#!/usr/bin/env python3
"""Benchmark service map performance impact with validated timing.

Measures investigation time with service map ON vs OFF.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


def clean_service_map():
    """Remove service map file."""
    service_map_path = Path("app/memories/service_map.json")
    if service_map_path.exists():
        service_map_path.unlink()
        print("  ✓ Cleaned service map")


def clean_memory_files(pattern: str):
    """Remove memory files matching pattern."""
    memories_dir = Path("app/memories")
    if memories_dir.exists():
        excluded = {
            "IMPLEMENTATION_PLAN.md",
            "FINDINGS.md",
            "SUCCESS.md",
            "BENCHMARK_RESULTS.md",
            "SERVICE_MAP_EXPERIMENTS.md",
            "SERVICE_MAP_README.md",
        }
        count = 0
        for f in memories_dir.glob("*.md"):
            if pattern in f.name and f.name not in excluded:
                f.unlink()
                count += 1
        if count > 0:
            print(f"  ✓ Cleaned {count} memory files")


def run_test_timed(test_module: str) -> tuple[float, bool]:
    """Run a test and return (elapsed_time, success)."""
    start = time.time()

    result = subprocess.run(
        [sys.executable, "-m", test_module],
        env={**os.environ, "TRACER_OUTPUT_FORMAT": "text"},
        capture_output=True,
        text=True,
    )

    elapsed = time.time() - start
    success = result.returncode == 0 and "TEST PASSED" in result.stdout

    return elapsed, success


def run_benchmark():
    """Run benchmark measuring service map impact."""
    print("\n" + "=" * 80)
    print("SERVICE MAP PERFORMANCE BENCHMARK (VALIDATED)")
    print("=" * 80)
    print("\nMeasures investigation time with service map ON vs OFF")
    print("Uses real test case: upstream_prefect_ecs_fargate")
    print("=" * 80)

    test_module = "tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e"
    runs_per_phase = 3

    # Phase 1: WITHOUT service map (cold start)
    print(f"\n{'='*80}")
    print("PHASE 1: WITHOUT Service Map (Cold Start)")
    print("=" * 80)
    print(f"Running {runs_per_phase} investigations with SERVICE_MAP_ENABLED=False\n")

    # Temporarily disable service map
    config_path = Path("app/agent/memory/service_map_config.py")
    original_config = config_path.read_text()
    config_path.write_text(original_config.replace("SERVICE_MAP_ENABLED = True", "SERVICE_MAP_ENABLED = False"))

    times_without = []
    for i in range(runs_per_phase):
        print(f"Run {i+1}/{runs_per_phase} (no service map):")
        clean_service_map()
        clean_memory_files("upstream_downstream_pipeline_prefect")

        elapsed, success = run_test_timed(test_module)
        times_without.append(elapsed)

        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  Time: {elapsed:.2f}s | {status}\n")

    avg_without = sum(times_without) / len(times_without)
    print(f"✓ Average time WITHOUT service map: {avg_without:.2f}s\n")

    # Phase 2: WITH service map (warm start)
    print(f"{'='*80}")
    print("PHASE 2: WITH Service Map (Warm Start)")
    print("=" * 80)

    # Restore service map
    config_path.write_text(original_config)

    print("Initial build (creates service map):")
    clean_service_map()
    clean_memory_files("upstream_downstream_pipeline_prefect")

    build_time, build_success = run_test_timed(test_module)
    status = "✅ PASS" if build_success else "❌ FAIL"
    print(f"  Time: {build_time:.2f}s | {status}")
    print(f"  ✓ Service map created\n")

    print(f"Running {runs_per_phase} investigations with SERVICE_MAP_ENABLED=True\n")

    times_with = []
    for i in range(runs_per_phase):
        print(f"Run {i+1}/{runs_per_phase} (with service map):")
        # Clean memory but keep service map
        clean_memory_files("upstream_downstream_pipeline_prefect")

        elapsed, success = run_test_timed(test_module)
        times_with.append(elapsed)

        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  Time: {elapsed:.2f}s | {status}\n")

    avg_with = sum(times_with) / len(times_with)
    print(f"✓ Average time WITH service map: {avg_with:.2f}s\n")

    # Results
    time_saved = avg_without - avg_with
    improvement_pct = (time_saved / avg_without * 100) if avg_without > 0 else 0

    print("=" * 80)
    print("VALIDATED RESULTS")
    print("=" * 80)
    print(f"\n{'Configuration':<40} {'Time':>10} {'Status':>10}")
    print("-" * 80)
    print(f"{'WITHOUT service map (cold start)':<40} {avg_without:>8.2f}s {'Baseline':>10}")
    print(f"{'WITH service map (warm start)':<40} {avg_with:>8.2f}s {'Optimized':>10}")
    print("-" * 80)
    print(f"{'Time saved per investigation':<40} {time_saved:>8.2f}s")
    print(f"{'Percentage improvement':<40} {improvement_pct:>8.1f}%")
    print("=" * 80)

    # Interpretation
    print("\n" + "=" * 80)
    print("INTERPRETATION")
    print("=" * 80)

    if improvement_pct >= 10:
        print(f"\n✅ Service map provides SIGNIFICANT speedup ({improvement_pct:.1f}%)\n")
        print(f"Time savings per investigation: {time_saved:.2f}s")
        print(f"\nCumulative impact:")
        print(f"  - 10 investigations:  {time_saved * 10:.1f}s saved ({time_saved * 10 / 60:.1f} min)")
        print(f"  - 50 investigations:  {time_saved * 50:.1f}s saved ({time_saved * 50 / 60:.1f} min)")
        print(f"  - 100 investigations: {time_saved * 100:.1f}s saved ({time_saved * 100 / 60:.1f} min)")
    elif 0 < improvement_pct < 10:
        print(f"\n⚠️  Service map provides MODEST speedup ({improvement_pct:.1f}%)\n")
        print(f"Time savings: {time_saved:.2f}s per investigation")
        print("\nPossible reasons for modest impact:")
        print("  - Investigation time dominated by API calls (not CPU/correlation)")
        print("  - Need more investigations to build useful hotspot data")
        print("  - Service map lookup/update overhead offsets some gains")
    elif improvement_pct < 0:
        print(f"\n❌ Service map appears SLOWER ({abs(improvement_pct):.1f}% regression)\n")
        print("This needs investigation - service map may have bugs or overhead issues")
    else:
        print(f"\n➖ Service map has NO MEASURABLE IMPACT\n")
        print("Performance is identical with/without service map")

    print("=" * 80)

    # Write results
    results = {
        "timestamp": datetime.now().isoformat(),
        "test_module": test_module,
        "runs_per_phase": runs_per_phase,
        "avg_without_service_map_seconds": round(avg_without, 2),
        "avg_with_service_map_seconds": round(avg_with, 2),
        "time_saved_seconds": round(time_saved, 2),
        "improvement_percent": round(improvement_pct, 1),
        "times_without": [round(t, 2) for t in times_without],
        "times_with": [round(t, 2) for t in times_with],
        "initial_build_time_seconds": round(build_time, 2),
    }

    # Write JSON results
    results_json = Path("app/memories/service_map_benchmark_results.json")
    results_json.write_text(json.dumps(results, indent=2))
    print(f"\n✓ Results written to: {results_json}")

    # Write markdown report
    results_md = Path("app/memories/SERVICE_MAP_BENCHMARK_RESULTS.md")
    results_md.write_text(
        f"""# Service Map Performance Benchmark (Validated)

**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Test Case**: upstream_prefect_ecs_fargate  
**Runs**: {runs_per_phase} per configuration

## Results

| Configuration | Average Time | Improvement |
|---------------|--------------|-------------|
| **WITHOUT service map** (cold start) | {avg_without:.2f}s | Baseline |
| **WITH service map** (warm start) | {avg_with:.2f}s | **{improvement_pct:+.1f}%** |
| **Time saved** | {time_saved:.2f}s | {improvement_pct:.1f}% faster |

## Detailed Timings

### WITHOUT Service Map (Cold Start)
{chr(10).join(f'- Run {i+1}: {t:.2f}s' for i, t in enumerate(times_without))}

**Average**: {avg_without:.2f}s

### WITH Service Map (Warm Start)
{chr(10).join(f'- Run {i+1}: {t:.2f}s' for i, t in enumerate(times_with))}

**Average**: {avg_with:.2f}s

### Initial Build
- First run (creates service map): {build_time:.2f}s

## Cumulative Impact

If improvement holds across investigations:
- **10 investigations**: {time_saved * 10:.1f}s saved ({time_saved * 10 / 60:.1f} minutes)
- **50 investigations**: {time_saved * 50:.1f}s saved ({time_saved * 50 / 60:.1f} minutes)
- **100 investigations**: {time_saved * 100:.1f}s saved ({time_saved * 100 / 60:.1f} minutes)

## Raw Data

```json
{json.dumps(results, indent=2)}
```
"""
    )
    print(f"✓ Report written to: {results_md}\n")

    return results


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    results = run_benchmark()
    sys.exit(0 if results["improvement_percent"] >= 0 else 1)
