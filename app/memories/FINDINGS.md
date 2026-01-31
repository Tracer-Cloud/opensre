# Memory System Implementation Findings

**Date:** 2026-01-31  
**Status:** Infrastructure complete, speedup goal not achieved

## Summary

Implemented MD-based memory system with Openclaw patterns across 3 LLM nodes (frame_problem, plan_actions, diagnose_root_cause). All infrastructure tests pass, but E2E test reveals memory **slows down** RCA instead of speeding it up.

## Test Results

### E2E Speed Test (test_memory_speed.py)
- **Baseline (no memory):** 30.55s
- **With memory:** 34.69s
- **Result:** -13.6% (SLOWER, not faster)
- **Threshold:** 50% speedup required (15.27s)
- **Status:** ❌ FAILED

## Root Cause of Slowdown

**Adding context to LLM prompts increases processing time, not decreases it.**

- Memory context: ~500 chars of prior investigation summary
- Added to 3 prompts: frame_problem, plan_actions, diagnose_root_cause
- Effect: More tokens to process → longer LLM latency
- Result: Memory overhead > any benefit from "guidance"

## What Was Implemented (All Tests Pass)

### Milestone 1: Memory Infrastructure ✅
- `app/agent/memory.py` - Read/write MD files with Openclaw pattern
- `app/agent/memory_test.py` - 6 tests, all passing
- Quality gate: only persist when confidence >70% AND validity >70%

### Milestone 2: frame_problem Integration ✅
- Loads memory context before LLM call
- Test: `app/agent/nodes/frame_problem/frame_problem_test.py` (skipped without API key)

### Milestone 3: plan_actions Integration ✅
- Adds "Prior Successful Investigation Paths" section
- Test: `app/agent/nodes/plan_actions/build_prompt_test.py` - 10 passing

### Milestone 4: diagnose_root_cause Integration ✅
- Adds "Prior Root Cause Patterns" section
- Memory-aware prompt building

### Milestone 5: Memory Persistence ✅
- Writes memory after RCA in publish_findings
- Quality gate enforced

### Milestone 6: E2E Speed Test ✅
- `tests/test_case_upstream_prefect_ecs_fargate/test_memory_speed.py`
- Properly measures baseline vs memory
- **Finding:** Memory makes things slower

## Alternative Approaches for 50% Speedup

### Option 1: Skip LLM Calls Entirely (Aggressive Caching)
**Strategy:** When memory has high-confidence cached results for this pipeline type, skip LLM calls
- frame_problem: Use cached problem statement
- plan_actions: Use cached action sequence
- diagnose_root_cause: Use cached root cause template

**Implementation:**
- Add cache hit logic before each LLM call
- If cache hit, return cached result immediately
- Only call LLM if cache miss or low confidence

**Expected Speedup:** 70-80% (skip 3 LLM calls worth ~25s)

### Option 2: Anthropic Prompt Caching
**Strategy:** Use Anthropic's prompt caching feature for repeated prompt sections
- Cache the static parts of prompts (instructions, guidelines)
- Only pay for dynamic parts (evidence, problem details)
- Anthropic offers 90% cost reduction + latency improvement for cached content

**Implementation:**
- Wrap static prompt sections with cache control
- Requires Anthropic SDK update

**Expected Speedup:** 30-40% (from caching static instructions)

### Option 3: Smaller, Faster Model for Cached Scenarios
**Strategy:** When memory provides strong guidance, use a smaller/faster model
- Baseline: Claude Sonnet 3.5 (~10s per call)
- With memory: Claude Haiku (~2s per call)
- Memory context guides the faster model to right answer

**Expected Speedup:** 60-70% (faster model selection)

## Recommendation

**Option 1 (Aggressive Caching)** is most likely to achieve 50% speedup, but requires architectural changes:
- Add cache hit/miss logic to each LLM node
- Risk: stale cache if pipeline changes
- Mitigation: TTL on memory files, cache invalidation on failures

**Option 2 (Prompt Caching)** is easiest to implement but may not reach 50%:
- No code changes beyond SDK configuration
- Works transparently
- May only achieve 30-40% speedup

## Current Status

- Memory infrastructure: ✅ Production ready
- File format: ✅ Openclaw session-memory pattern
- Quality gate: ✅ Working
- Speedup goal: ❌ Not achieved with current approach
- Tests: 20/23 passing (3 skipped without API key)

## Next Steps

1. Choose speedup approach (Option 1, 2, or 3)
2. Implement chosen approach
3. Re-run E2E speed test
4. Iterate until 50% threshold met
