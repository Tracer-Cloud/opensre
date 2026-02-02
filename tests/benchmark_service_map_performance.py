"""Benchmark service map performance impact.

Validates time savings claims by measuring investigation time with service map ON vs OFF.
"""

import os
import time
from pathlib import Path

# Import test fixtures
from tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e import (
    create_test_alert as create_prefect_alert,
)

from app.agent.graph_pipeline import run_investigation
from app.agent.memory.io import get_memories_dir


def clean_service_map():
    """Remove service map file."""
    service_map_path = get_memories_dir() / "service_map.json"
    if service_map_path.exists():
        service_map_path.unlink()


def clean_memory_files(pipeline_name: str):
    """Remove memory files for a pipeline."""
    memories_dir = get_memories_dir()
    if memories_dir.exists():
        for f in memories_dir.glob(f"*{pipeline_name}*.md"):
            if f.name not in (
                "IMPLEMENTATION_PLAN.md",
                "FINDINGS.md",
                "SUCCESS.md",
                "BENCHMARK_RESULTS.md",
                "SERVICE_MAP_EXPERIMENTS.md",
                "SERVICE_MAP_README.md",
            ):
                f.unlink()


def run_single_investigation_timed(alert_dict: dict) -> float:
    """Run investigation and return elapsed time."""
    start = time.time()
    run_investigation(
        alert_name=alert_dict["alert_name"],
        pipeline_name=alert_dict["pipeline_name"],
        severity=alert_dict["severity"],
        raw_alert=alert_dict["raw_alert"],
    )
    return time.time() - start


def benchmark_service_map_impact():
    """Benchmark service map impact on investigation time."""
    print("\n" + "=" * 80)
    print("SERVICE MAP PERFORMANCE BENCHMARK")
    print("=" * 80)

    # Phase 1: Cold start WITHOUT service map (3 runs)
    print("\nPhase 1: WITHOUT service map (cold start) - 3 runs")
    print("-" * 80)

    # Disable service map
    import app.agent.memory.service_map_config as config

    config.SERVICE_MAP_ENABLED = False

    times_without = []
    for i in range(3):
        print(f"\nRun {i+1}/3 (cold start)...")
        clean_service_map()
        clean_memory_files("upstream_downstream_pipeline_prefect")

        alert_dict = create_prefect_alert()
        elapsed = run_single_investigation_timed(alert_dict)
        times_without.append(elapsed)
        print(f"  Time: {elapsed:.2f}s")

    avg_without = sum(times_without) / len(times_without)
    print(f"\n✓ Average WITHOUT service map (cold): {avg_without:.2f}s")

    # Phase 2: Initial run WITH service map (builds the map)
    print(f"\nPhase 2: Initial run WITH service map (builds map)")
    print("-" * 80)

    config.SERVICE_MAP_ENABLED = True
    clean_service_map()
    clean_memory_files("upstream_downstream_pipeline_prefect")

    alert_dict = create_prefect_alert()
    build_time = run_single_investigation_timed(alert_dict)
    print(f"  Time: {build_time:.2f}s (includes service map creation)")

    # Phase 3: Warm start WITH service map (3 runs)
    print(f"\nPhase 3: WITH service map (warm start) - 3 runs")
    print("-" * 80)

    times_with = []
    for i in range(3):
        print(f"\nRun {i+1}/3 (warm start with existing service map)...")
        # Clean memory but keep service map
        clean_memory_files("upstream_downstream_pipeline_prefect")

        alert_dict = create_prefect_alert()
        elapsed = run_single_investigation_timed(alert_dict)
        times_with.append(elapsed)
        print(f"  Time: {elapsed:.2f}s")

    avg_with = sum(times_with) / len(times_with)
    print(f"\n✓ Average WITH service map (warm): {avg_with:.2f}s")

    # Results
    print(f"\n{'='*80}")
    print("RESULTS")
    print(f"{'='*80}")
    print(f"Average WITHOUT service map (cold): {avg_without:.2f}s")
    print(f"Average WITH service map (warm):    {avg_with:.2f}s")
    print(f"Time saved per investigation:       {avg_without - avg_with:.2f}s")
    print(f"Percentage improvement:             {((avg_without - avg_with) / avg_without * 100):.1f}%")

    if avg_without > avg_with:
        print(f"\n✅ Service map provides measurable speedup!")
    elif abs(avg_without - avg_with) < 1.0:
        print(f"\n⚠️  Service map has minimal impact (< 1s difference)")
    else:
        print(f"\n❌ Service map may be slower (needs investigation)")

    print(f"{'='*80}\n")

    # Detailed breakdown
    print("Detailed Timings:")
    print(f"  WITHOUT (cold start): {', '.join(f'{t:.2f}s' for t in times_without)}")
    print(f"  WITH (warm start):    {', '.join(f'{t:.2f}s' for t in times_with)}")
    print(f"  Initial build:        {build_time:.2f}s")

    # Restore config
    config.SERVICE_MAP_ENABLED = True

    return {
        "avg_without_service_map": avg_without,
        "avg_with_service_map": avg_with,
        "time_saved": avg_without - avg_with,
        "improvement_percent": ((avg_without - avg_with) / avg_without * 100),
        "times_without": times_without,
        "times_with": times_with,
        "initial_build_time": build_time,
    }


if __name__ == "__main__":
    # Set output format to avoid Rich overhead
    os.environ["TRACER_OUTPUT_FORMAT"] = "text"
    os.environ["TRACER_MEMORY_ENABLED"] = "1"

    results = benchmark_service_map_impact()

    # Write results to file
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    results_file = get_memories_dir() / f"SERVICE_MAP_BENCHMARK_{timestamp}.md"

    content = f"""# Service Map Performance Benchmark

**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**Pipeline**: upstream_downstream_pipeline_prefect
**Runs**: 3 per configuration

## Results

| Metric | Value |
|--------|-------|
| Average WITHOUT service map (cold) | {results['avg_without_service_map']:.2f}s |
| Average WITH service map (warm) | {results['avg_with_service_map']:.2f}s |
| Time saved per investigation | {results['time_saved']:.2f}s |
| Percentage improvement | {results['improvement_percent']:.1f}% |

## Detailed Timings

### WITHOUT Service Map (Cold Start)
{chr(10).join(f'- Run {i+1}: {t:.2f}s' for i, t in enumerate(results['times_without']))}

### WITH Service Map (Warm Start)  
{chr(10).join(f'- Run {i+1}: {t:.2f}s' for i, t in enumerate(results['times_with']))}

### Initial Build
- First run with service map: {results['initial_build_time']:.2f}s

## Interpretation

"""

    if results["improvement_percent"] > 5:
        content += f"""✅ **Service map provides measurable speedup of {results['improvement_percent']:.1f}%**

The {results['time_saved']:.2f}s time savings per investigation compounds over time:
- 10 investigations: {results['time_saved'] * 10:.1f}s ({results['time_saved'] * 10 / 60:.1f} minutes) saved
- 100 investigations: {results['time_saved'] * 100:.1f}s ({results['time_saved'] * 100 / 60:.1f} minutes) saved

The speedup comes from:
- Skipping redundant asset discovery (assets already in map)
- Hotspot prioritization (check frequently-failing assets first)
- Avoiding re-correlation of known relationships
"""
    elif abs(results["improvement_percent"]) < 5:
        content += f"""⚠️ **Service map has minimal impact ({results['improvement_percent']:.1f}%)**

Possible reasons:
- Investigation time dominated by evidence collection (API calls)
- Service map lookup/update overhead ~= time saved
- Need more investigations to build useful hotspot data

Consider:
- Measure after 10+ investigations when hotspot data is richer
- Profile LLM call time vs evidence collection time
- Check if memory system is already providing similar benefits
"""
    else:
        content += f"""❌ **Service map appears slower ({results['improvement_percent']:.1f}% regression)**

This needs investigation:
- Check service map update overhead
- Profile JSON serialization time
- Verify no bugs in update logic
- Compare with/without memory enabled
"""

    content += f"""
## Raw Data

```json
{json.dumps(results, indent=2)}
```
"""

    results_file.write_text(content)
    print(f"\n✓ Results written to: {results_file.name}")


import json
