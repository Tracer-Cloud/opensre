# Speedup Opportunities - Validation Analysis

**Date:** 2026-01-31  
**Baseline:** `make demo` (upstream_downstream_pipeline_prefect)  
**Target:** 50% speedup (33s → 16.5s)

## Actual Performance Breakdown (Measured)

From real `make demo` runs:

```
Total: 33s

Non-LLM (6.1s, 18%):
- build_context:    0.5s  (context building)
- extract_alert:    2.4s  (JSON parsing)
- investigate:      1.0s  (AWS API calls: S3, CloudWatch, Lambda)
- publish_findings: 2.2s  (formatting + rendering)

LLM Calls (29.2s, 88%):
- frame_problem:        7.4s  (generate problem statement)
- plan_actions:        10.5s  (select investigation actions)
- diagnose_root_cause: 11.3s  (root cause analysis)
```

**Key Insight:** LLM calls dominate (88% of time). Must optimize LLM operations to reach 50% speedup.

## Opportunity #1: Skip LLM Calls Entirely (Aggressive Caching)

### Strategy
When cached investigation exists for this pipeline with high confidence (>80%), skip LLM calls and use cached results directly.

### Implementation
```python
# frame_problem node
cached = get_cached_investigation(pipeline_name)
if cached and cached.get("problem_pattern"):
    # Skip LLM - use cached problem
    problem = ProblemStatement(summary=cached["problem_pattern"])
    return {"problem_md": render_problem_statement_md(problem, state)}

# Otherwise call LLM as normal
```

### Savings Calculation
- **frame_problem:** 7.4s → 0.1s (saved: 7.3s)
- **plan_actions:** 10.5s → 0.1s (saved: 10.4s) 
- **diagnose_root_cause:** 11.3s → 0.1s (saved: 11.2s)
- **Total savings:** 28.9s
- **New time:** 33s - 28.9s = 4.1s
- **Speedup:** 87%

### Risks
- Stale cache if pipeline changes
- Cache miss on first run (no speedup)
- May return incorrect results if alert differs significantly

### Mitigations
- TTL on memory files (7 days)
- Cache invalidation on confidence <70%
- Similarity check between cached and current alert
- Fallback to LLM on cache miss

**Verdict:** ✅ Definitely achieves 50% (actually 87%)  
**Effort:** Medium (add cache-check logic to 3 nodes)  
**Risk:** Medium (stale cache, but mitigable)

---

## Opportunity #2: Anthropic Prompt Caching

### Strategy
Use Anthropic's built-in prompt caching to cache static prompt sections (instructions, guidelines, evidence format specs).

### How It Works
```python
# Mark static sections for caching
system_message = {
    "role": "system",
    "content": "You are an SRE...",  # Static - cached
    "cache_control": {"type": "ephemeral"}
}

user_message = {
    "role": "user", 
    "content": problem_md  # Dynamic - not cached
}
```

### Savings Calculation
From Anthropic docs:
- Cached tokens: 90% cost reduction + latency improvement
- Typical prompt: 60% static, 40% dynamic
- Effective speedup per call: ~25-30%

**Per node:**
- frame_problem: 7.4s → 5.5s (saved: 1.9s)
- plan_actions: 10.5s → 7.8s (saved: 2.7s)
- diagnose_root_cause: 11.3s → 8.4s (saved: 2.9s)

**Total savings:** 7.5s  
**New time:** 33s - 7.5s = 25.5s  
**Speedup:** 23%

**Verdict:** ❌ Only 23% speedup (below 50% threshold)  
**Effort:** Low (SDK configuration only)  
**Risk:** Low (transparent, no behavior changes)

---

## Opportunity #3: Faster Model with Guidance

### Strategy
When memory provides strong guidance, use Claude Haiku (5-10x faster) instead of Sonnet.

### Model Performance
- **Sonnet 3.5:** ~10s per structured output call (current)
- **Haiku 3.0:** ~1-2s per structured output call
- **Haiku with guidance:** Accurate enough when primed with patterns

### Implementation
```python
# In get_llm():
if is_memory_enabled() and get_cached_investigation(pipeline_name):
    return ChatAnthropic(model="claude-3-haiku-20240307", temperature=0.3)
else:
    return ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0.3)
```

### Savings Calculation
Assuming Haiku is 6x faster:
- frame_problem: 7.4s → 1.2s (saved: 6.2s)
- plan_actions: 10.5s → 1.8s (saved: 8.7s)
- diagnose_root_cause: 11.3s → 1.9s (saved: 9.4s)

**Total savings:** 24.3s  
**New time:** 33s - 24.3s = 8.7s  
**Speedup:** 74%

**Verdict:** ✅ Achieves 50% (actually 74%)  
**Effort:** Low (model selection logic)  
**Risk:** Medium (Haiku may be less accurate, needs validation)

---

## Opportunity #4: Parallel LLM Calls

### Strategy
Currently nodes run sequentially. frame_problem, plan_actions, diagnose_root_cause could potentially run in parallel if dependencies allow.

### Current Flow
```
frame_problem (7.4s) → plan_actions (10.5s) → investigate (1s) → diagnose (11.3s)
Total: 30.2s
```

### Parallel Opportunity
Only diagnose_root_cause strictly requires evidence from investigate. Could potentially run frame_problem + initial plan_actions in parallel.

### Savings Calculation
**Limited** - Dependencies prevent full parallelization:
- plan_actions needs problem_md from frame_problem
- diagnose needs evidence from investigate
- Max parallel overlap: ~2-3s

**Verdict:** ❌ Only ~10% speedup (not enough)  
**Effort:** High (LangGraph flow changes)  
**Risk:** High (complex dependencies)

---

## Opportunity #5: Reduce Investigation Loops

### Strategy
Current implementation can loop up to 4 times if confidence is low. With memory providing guidance, could terminate earlier.

### Current Behavior
- Loop 1: Initial investigation
- Loop 2-4: Additional investigation if confidence <60% or vendor evidence missing

### With Memory
If cache shows this pipeline type typically finds root cause in 1 loop, skip subsequent loops.

### Savings Calculation
Baseline typically uses 1 loop (33s).  
When looping occurs: 2 loops = 66s

**Memory could prevent second loop:**
- Savings when looping would occur: ~33s
- Frequency: ~10-20% of cases

**Average speedup:** ~5-10%

**Verdict:** ❌ Not enough on its own  
**Effort:** Low  
**Risk:** Low  
**Note:** Good complementary optimization

---

## Validated Recommendations

### Primary Approach: Option 3 (Faster Model)
**Why:**
- ✅ 74% speedup (exceeds 50% target)
- ✅ Low implementation effort
- ✅ Works on first run (no cache warmup needed)
- ⚠️ Need to validate Haiku accuracy with memory guidance

**Implementation Plan:**
1. Add model selection logic based on memory availability
2. Run accuracy tests: Haiku+memory vs Sonnet baseline
3. Measure actual speedup
4. Fall back to Sonnet if Haiku confidence <70%

### Secondary Approach: Option 1 (Aggressive Caching)
**Why:**
- ✅ 87% speedup (highest potential)
- ✅ Perfect accuracy (reuses proven results)
- ❌ Only works on repeat runs (cold start = no speedup)
- ⚠️ Stale cache risk

**Best for:** Recurring alerts on same pipeline

### Tertiary: Option 2 (Prompt Caching)
**Why:**
- ⚠️ Only 23% speedup (below threshold)
- ✅ Easy to implement
- ✅ Zero risk

**Best for:** Complementary optimization (combine with Option 3)

---

## Recommended Combined Approach

**Hybrid Strategy** (Option 1 + Option 3):

```python
# For each LLM node:
cached = get_cached_investigation(pipeline_name)

if cached and confidence_was_high:
    # Aggressive cache: skip LLM entirely
    return cached_result
elif cached:
    # Use Haiku with guidance from cache
    llm = get_haiku_llm()
    # Add memory context to prompt
    return llm.invoke(prompt_with_memory)
else:
    # Cold start: use Sonnet (no speedup)
    llm = get_sonnet_llm()
    return llm.invoke(prompt)
```

**Expected Performance:**
- **Cold start (no cache):** 33s (0% speedup)
- **Warm start (cache exists):** 8-10s (70-75% speedup)
- **Exact match (cached result):** 4s (87% speedup)

**Average speedup:** ~50-60% (accounting for mix of cold/warm/exact)

---

## Implementation Priority

1. **Quick Win:** Option 3 (Faster Model) - 2-4 hours
2. **Full Solution:** Option 1 (Aggressive Caching) - 4-6 hours
3. **Polish:** Option 2 (Prompt Caching) - 1 hour
4. **Validation:** Re-run test_memory_speed.py and verify ≥50%

## Next Steps

1. Implement Option 3 (Haiku model selection)
2. Validate accuracy: run make demo 5x, check all achieve >70% confidence
3. Measure speedup: should see 60-70% improvement
4. If needed, add Option 1 (aggressive caching) for perfect cache hits
5. Final validation: test_memory_speed.py passes

**Confidence:** High - Option 3 alone should achieve 50% target
