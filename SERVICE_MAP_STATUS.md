# Service Map - Current Status

## Executive Summary

**Status**: ✅ Implemented and tested, ⚠️ DEFAULT OFF due to performance overhead

**Reason**: Validated benchmarks show 16.5% overhead with no offsetting speedup. Service map tracks assets/edges correctly but doesn't yet optimize investigation paths.

## Validated Performance (2026-02-01)

| Metric | WITHOUT | WITH | Impact |
|--------|---------|------|--------|
| **Average time** | 30.09s | 35.05s | **-16.5%** ❌ |
| **Time variance** | ±2.2s | ±7.3s | Higher |
| **Consistency** | Good | Variable | |

**Conclusion**: Service map currently adds overhead without speedup benefits.

## What Works ✅

1. **Asset Discovery** (635 lines)
   - 8 asset types tracked correctly
   - AWS-native IDs (lambda:name, s3_bucket:name)
   - Deduplication works (S3 buckets merged)

2. **Edge Inference** (2.5x improvement over V1)
   - 3 edge types: triggers, writes_to, runs_on
   - Evidence-first parsing (direct field extraction)
   - Evidence traceability (edges track proof source)

3. **Hotspot Tracking**
   - External API identified as 2x hotspot ✅
   - Investigation count increments correctly
   - Last_investigated timestamps accurate

4. **Memory Integration**
   - Compact embedding in memory files
   - Asset Inventory + Service Map JSON sections
   - Parser extracts sections correctly

5. **Tests**
   - 17/17 tests passing
   - Comprehensive coverage
   - All linting clean

## What Doesn't Work ❌

1. **Performance Impact**
   - Adds 4.95s overhead per investigation
   - 16.5% slower than baseline
   - High variance (28s to 44s runs)

2. **No Action Optimization**
   - Service map is built but not consulted during planning
   - All actions execute as if map doesn't exist
   - Pure tracking overhead with no benefit

3. **No Hotspot Prioritization**
   - Hotspots identified but not used
   - Investigation order unchanged
   - No early-exit if hotspot is root cause

## Path to Speedup

### Required Changes (Before Enabling by Default)

#### 1. Action Skipping Based on Known Assets
```python
# In plan_actions node
def select_actions_with_service_map(alert, service_map):
    planned_actions = default_actions(alert)
    
    # Skip S3 inspection if asset known with high confidence
    s3_bucket = alert.annotations.get("landing_bucket")
    if service_map.has_asset(f"s3_bucket:{s3_bucket}", min_confidence=0.9):
        planned_actions.remove("inspect_s3_object")
        print("[SERVICE_MAP] Skipping S3 inspection (known asset)")
    
    # Skip Lambda inspection if known
    lambda_fn = alert.annotations.get("function_name")
    if service_map.has_asset(f"lambda:{lambda_fn}", min_confidence=0.9):
        planned_actions.remove("inspect_lambda_function")
        print("[SERVICE_MAP] Skipping Lambda inspection (known asset)")
    
    return planned_actions
```

**Estimated impact**: Save 2-3 API calls = 5-8s saved

#### 2. Hotspot Prioritization
```python
# In plan_actions node
def prioritize_by_hotspots(planned_actions, service_map):
    hotspots = service_map.find_hotspots(min_count=3)
    
    # If External API is a hotspot (3+ investigations), check it first
    for hotspot in hotspots:
        if hotspot.type == "external_api":
            planned_actions.insert(0, "check_external_api_audit")
            print(f"[SERVICE_MAP] Prioritizing hotspot: {hotspot.name}")
    
    return planned_actions
```

**Estimated impact**: Find root cause faster = 2-4s saved

#### 3. Early Exit on Hotspot Confirmation
```python
# In root_cause_diagnosis
if hotspot_confirms_root_cause(evidence, service_map):
    # Don't need further investigation
    return early_exit_with_high_confidence()
```

**Estimated impact**: Skip 30-50% of planned actions = 10-15s saved

### Net Result After Optimization

```
Current: -16.5% (4.95s overhead)
With action skipping: +15% (4.5s saved)
With hotspot prioritization: +10% (3s saved)
Net improvement: +25-30% speedup (7-9s saved)
```

## Current Configuration

**File**: `app/agent/memory/service_map_config.py`
```python
SERVICE_MAP_ENABLED = False  # DEFAULT OFF until optimization implemented
```

**To enable** (for development/testing):
```python
SERVICE_MAP_ENABLED = True
```

## Recommendations

### For Production

**DO NOT enable by default yet**. Service map adds overhead without benefits.

**Alternative approaches**:
1. **Wait for optimization** (action-skipping + prioritization)
2. **Enable for specific customers** (with many investigations, hotspot data is valuable)
3. **Enable for analysis only** (track assets but don't use in hot path)

### For Development

**Keep building**:
- Infrastructure is solid
- Edge inference works (2.5x improvement)
- Hotspot tracking works
- Ready for optimization layer

**Next milestones**:
1. Implement action skipping (Week 1)
2. Implement hotspot prioritization (Week 2)
3. Re-benchmark and validate +15-25% speedup (Week 3)
4. Enable by default if speedup validated (Week 4)

## Validated Claims

### ❌ False Claims (Remove from docs)
```
"Saves 50% of investigation time"
"Reduces 5-10 minute investigations to 2-3 minutes"
"Skip correlation steps immediately"
```

### ✅ True Claims (Keep in docs)
```
"Tracks assets and edges accurately (2.5x more edges than V1)"
"Identifies hotspots across investigations (External API = 2x)"
"Enriches memory with infrastructure knowledge"
"Provides foundation for future optimization"
```

### ⚠️ Conditional Claims (Add context)
```
"Can enable 25-30% speedup" → "...after action-skipping is implemented"
"Skips known correlations" → "...once integrated with plan_actions node"
"Prioritizes hotspots" → "...when prioritization logic is added"
```

## Files to Update

1. **SERVICE_MAP_SUMMARY.md** - Remove unvalidated time savings claims
2. **SERVICE_MAP_V2_IMPROVEMENTS.md** - Add caveat about performance
3. **IMPLEMENTATION_COMPLETE.md** - Update with validated benchmarks
4. **service_map_config.py** - ✅ Already set to False

## Timeline

### Current Status (Feb 1, 2026)
- ✅ V1 implemented (hotspots, basic edge inference)
- ✅ V2 implemented (evidence-first parsing, 2.5x more edges)
- ✅ Benchmarks validated (16.5% overhead measured)
- ⚠️ DEFAULT OFF until optimization

### Projected Timeline
- Week 1-2: Implement action skipping + hotspot prioritization
- Week 3: Re-benchmark (target: +15-25% speedup)
- Week 4: Enable by default if speedup validated
- Month 2+: Parallel investigation for multi-pipeline incidents

## Conclusion

**The service map is technically sound but not yet performance-positive.** It's **infrastructure for future speedup**, not a speedup itself.

**Honest assessment**:
- Built: ✅ Yes (1,526 lines of code)
- Tested: ✅ Yes (17/17 passing)
- Fast: ❌ No (16.5% overhead currently)
- Valuable: ✅ Yes (hotspot tracking, learning foundation)
- Production-ready: ⚠️ Not for default-ON, but ready for opt-in

**The validated benchmark prevents us from shipping false performance claims.** Better to know now and fix it properly than ship with inflated expectations.
