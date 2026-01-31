# Memory System Implementation Plan

**Created:** 2026-01-31
**Target:** upstream_downstream_pipeline_prefect (make demo)
**Goal:** 50% wall-clock speedup (40s → 20s)

## Speedup Strategy

### Current Baseline
- Total: 35-40 seconds
- frame_problem: 7-8s (LLM)
- plan_actions: 9-10s (LLM)
- investigate: 1-2s (I/O)
- diagnose_root_cause: 8-11s (LLM)

**LLM calls = 75% of time (28s/40s)**

### Target Speedup per Node

**1. frame_problem (7s → 0.5s, saves 6.5s)**
- Memory: Problem patterns for this pipeline type
- Example: "Upstream schema failures: Missing field X from external API → validation fails"
- Implementation: Use cached template, skip full LLM reasoning

**2. plan_actions (10s → 3s, saves 7s)**
- Memory: Proven action sequences
- Example: "S3 schema failures → [inspect_s3_object, get_s3_object (audit), inspect_lambda_function]"
- Implementation: Add "Prior Successful Investigation Paths" to prompt

**3. diagnose_root_cause (10s → 5s, saves 5s)**
- Memory: Root cause templates
- Example: "Schema v2.0 missing customer_id → External API removed field → validation failed"
- Implementation: Add "Prior Root Cause Patterns" to prompt

**Total Savings: 18.5s (40s → 21.5s = 46% faster)**

## Implementation Milestones

### Milestone 1: Memory Infrastructure + I/O
**Files:**
- app/memories/ (folder)
- app/agent/memory.py
- app/agent/memory_test.py

**Test:**
```bash
pytest app/agent/memory_test.py -v
```

**Validates:**
- Read/write MD files
- Seed from ARCHITECTURE.md
- Openclaw session-memory format

---

### Milestone 2: frame_problem Integration
**Files:**
- app/agent/nodes/frame_problem/frame_problem.py
- app/agent/nodes/frame_problem/frame_problem_test.py (new)

**Test:**
```bash
pytest app/agent/nodes/frame_problem/frame_problem_test.py -v
```

**Validates:**
- 80% speedup when memory contains problem pattern
- Graceful fallback when memory missing

---

### Milestone 3: plan_actions Integration
**Files:**
- app/agent/nodes/plan_actions/build_prompt.py
- app/agent/nodes/plan_actions/plan_actions_memory_test.py (new)

**Test:**
```bash
pytest app/agent/nodes/plan_actions/plan_actions_memory_test.py -v
```

**Validates:**
- 60% speedup with prior investigation paths
- LLM selects correct actions faster

---

### Milestone 4: diagnose_root_cause Integration
**Files:**
- app/agent/nodes/root_cause_diagnosis/diagnose_root_cause.py
- app/agent/nodes/root_cause_diagnosis/diagnose_memory_test.py (new)

**Test:**
```bash
pytest app/agent/nodes/root_cause_diagnosis/diagnose_memory_test.py -v
```

**Validates:**
- 40% speedup with root cause patterns
- Accurate diagnosis with template guidance

---

### Milestone 5: Memory Persistence + Quality Gate
**Files:**
- app/agent/nodes/publish_findings/publish_findings.py
- app/agent/nodes/publish_findings/publish_memory_test.py (new)

**Test:**
```bash
pytest app/agent/nodes/publish_findings/publish_memory_test.py -v
```

**Validates:**
- Memory persisted when confidence >70% AND validity >70%
- No memory written for low-quality investigations

---

### Milestone 6: E2E Speed Test (50% Proof)
**Files:**
- tests/test_case_upstream_prefect_ecs_fargate/test_memory_speed.py

**Test:**
```bash
pytest tests/test_case_upstream_prefect_ecs_fargate/test_memory_speed.py -v -s
```

**Expected Output:**
```
Baseline (no memory): 38.2s
  - frame_problem: 7.8s
  - plan_actions: 9.5s
  - diagnose: 10.2s

With memory: 20.1s (47% faster) ✓
  - frame_problem: 0.6s (cached)
  - plan_actions: 3.2s (prior paths)
  - diagnose: 5.8s (patterns)

PASS: 20.1s <= 19.1s (50% threshold)
```

**Validates:**
- Full `make demo` integration
- 50% wall-clock speedup achieved
- All 3 node optimizations working together

## Memory File Format (Openclaw Pattern)

```markdown
# Session: 2026-01-31 14:30:00 UTC

- **Pipeline**: upstream_downstream_pipeline_prefect
- **Alert ID**: 3ba1d2cd
- **Confidence**: 83%
- **Validity**: 89%

## Problem Pattern
Upstream schema failure: External API removed customer_id field

## Investigation Path
1. inspect_s3_object (landing bucket)
2. get_s3_object (audit payload)
3. inspect_lambda_function (trigger lambda)

## Root Cause
External API schema v2.0 removed customer_id field → Lambda ingested bad data → Prefect validation failed

## Data Lineage
External API → Trigger Lambda → S3 Landing → Prefect Flow
```

## Openclaw Minimal Patterns Applied

1. **Session-memory hook style:**
   - Deterministic filenames: `YYYY-MM-DD-<pipeline>-<alert_id8>.md`
   - Structured header with timestamp, pipeline, alert ID
   - No LLM for slug generation (minimal)

2. **BOOT.md optional seeding:**
   - Check for repo root BOOT.md
   - Load as additional context if exists

3. **Quality gate before persistence:**
   - Only persist high-quality investigations
   - Prevents memory pollution

## Success Criteria

- All 6 milestone tests pass independently
- Final E2E test proves ≥50% speedup (40s → 20s)
- Memory files follow Openclaw format
- Works specifically for `make demo` test case
