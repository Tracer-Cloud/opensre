# Duplication and Verbosity Analysis

## Executive Summary

This analysis identifies **actual duplications** and verbosity issues, while acknowledging that the current two-step flow (gathering → analysis) is **good architectural design** that maintains proper separation of concerns.

**Key Finding:** Gathering evidence and analyzing evidence are **fundamentally different operations** - not duplication. The separation should be maintained.

## Current Flow

```
frame_problem → investigate → diagnose_root_cause → publish_findings
```

Note: The sections below reference the previous flow for historical context.

### 1. Architectural Separation (NOT Duplication) ✅

**Location:** `hypothesis_execution/hypothesis_execution.py`

- **Function:** `gather_evidence_for_context()` (lines 23-120)
- **Responsibility:** **Data Gathering** (data layer)
- **What it does:** 
  - Calls multiple tools: `get_batch_statistics`, `get_failed_tools`, `get_failed_jobs`, `get_error_logs`, `get_host_metrics`, `get_airflow_metrics`
  - Gathers raw evidence data from APIs
  - Stores in `evidence` dict with keys like `failed_tools`, `failed_jobs`, `error_logs`, `all_logs`, `host_metrics`, `airflow_metrics`

**Location:** `diagnose_root_cause/investigate.py`

- **Function:** `perform_deep_investigation()` (lines 13-86)
- **Responsibility:** **Data Interpretation** (interpretation layer)
- **What it does:**
  - Reads evidence from state (already gathered by `hypothesis_execution`)
  - Calls analysis functions: `analyze_logs()`, `analyze_tools()`, `analyze_job_failures()`, `analyze_metrics()`
  - Builds causal chain from analyzed evidence
  - Returns investigation summary with patterns, correlations, and insights

**✅ This is GOOD design:** The separation maintains single responsibility:
- `hypothesis_execution`: "Here's what I found" (pure data)
- `diagnose_root_cause`: "Here's what it means" (interpretation)

**These are NOT the same operation:**
```python
# Gathering (hypothesis_execution) - Data collection
evidence = {
    "failed_jobs": [{"name": "job1", "exit_code": 137}],
    "error_logs": ["OOM killed", "Signal 9"],
}

# Analysis (diagnose_root_cause) - Interpretation
analysis = {
    "root_cause": "Out of memory - exit code 137 indicates OOM killer",
    "evidence_chain": "Exit code 137 → OOM logs → Memory exceeded",
    "confidence": 0.9,
    "patterns": ["memory_issue", "container_termination"]
}
```

### 2. Verbosity Issues

#### A. Verbose Evidence Gathering Code

**In `hypothesis_execution`:**
```python
# Lines 53-115: Multiple try/except blocks calling tools
try:
    batch_stats = get_batch_statistics(trace_id)
    evidence["batch_stats"] = batch_stats
except Exception:
    pass
# ... repeated for each tool (120+ lines total)
```

**Issue:** 120+ lines of repetitive try/except blocks. This could be simplified with a tool registry pattern or agent-based approach.

**Note:** The analysis step in `diagnose_root_cause` is NOT redundant - it performs interpretation, not just re-processing.

#### B. Duplicate Source Tracking Logic

**In `generate_hypotheses/generate_hypotheses.py`:**
```python
# Lines 52-63: Track executed sources
executed_hypotheses = state.get("executed_hypotheses", [])
executed_sources_set = set()
for h in executed_hypotheses:
    sources = h.get("sources", [])
    if isinstance(sources, list):
        executed_sources_set.update(sources)
    single_source = h.get("source")
    if single_source:
        executed_sources_set.add(single_source)
```

**In `hypothesis_execution/hypothesis_execution.py`:**
```python
# Lines 168-176: Same logic repeated
executed_hypotheses = state.get("executed_hypotheses", [])
executed_sources_set = set()
for h in executed_hypotheses:
    sources = h.get("sources", [])
    if isinstance(sources, list):
        executed_sources_set.update(sources)
    single_source = h.get("source")
    if single_source:
        executed_sources_set.add(single_source)
```

**Issue:** Identical code for tracking executed sources in two places.

### 3. Actual Duplications

#### A. Duplicate Source Tracking Logic ✅

**In `generate_hypotheses/generate_hypotheses.py` (lines 52-63):**
```python
executed_hypotheses = state.get("executed_hypotheses", [])
executed_sources_set = set()
for h in executed_hypotheses:
    sources = h.get("sources", [])
    if isinstance(sources, list):
        executed_sources_set.update(sources)
    single_source = h.get("source")
    if single_source:
        executed_sources_set.add(single_source)
```

**In `hypothesis_execution/hypothesis_execution.py` (lines 168-176):**
```python
executed_hypotheses = state.get("executed_hypotheses", [])
executed_sources_set = set()
for h in executed_hypotheses:
    sources = h.get("sources", [])
    if isinstance(sources, list):
        executed_sources_set.update(sources)
    single_source = h.get("source")
    if single_source:
        executed_sources_set.add(single_source)
```

**✅ This IS duplication** - identical code in two places. Should be extracted to a utility function.

#### B. Inconsistent Evidence Source Tracking

**`hypothesis_execution` (lines 198-217):**
- Merges evidence from context
- Updates `tracer_web_run` and `pipeline_run` with runtime evidence
- Sets defaults for `s3` and `batch_jobs`

**`diagnose_root_cause/investigate.py` (lines 38-49):**
- Checks which evidence sources were checked
- Tracks `evidence_sources_checked` and `evidence_sources_skipped`
- Maps evidence keys to source names

**Issue:** Two different tracking formats for the same information. Should be standardized.

### 4. Code Quality Issues

#### A. Verbose Evidence Gathering

**Current:** 120+ lines of try/except blocks in `gather_evidence_for_context()`

**Could be improved with:**
- Tool registry pattern
- Agent-based approach with dynamic tool selection
- Better error handling abstraction

#### B. Evidence Format Inconsistencies

Different parts of the codebase expect different evidence formats:
- Some expect `failed_jobs` as list
- Some expect `evidence_sources_checked` as list
- Some track sources differently

**Should:** Standardize evidence schema across nodes.

## Recommendations

### ✅ 1. Extract Common Logic (ACTUAL Duplication)

**Create utility function for source tracking:**
```python
# src/agent/utils/state_helpers.py
def get_executed_sources(state: InvestigationState) -> set[str]:
    """Extract all executed sources from hypotheses history."""
    executed_sources_set = set()
    for h in state.get("executed_hypotheses", []):
        if isinstance(h.get("sources"), list):
            executed_sources_set.update(h["sources"])
        if h.get("source"):
            executed_sources_set.add(h["source"])
    return executed_sources_set
```

**Usage:**
```python
# In generate_hypotheses.py
executed_sources_set = get_executed_sources(state)

# In hypothesis_execution.py
executed_sources_set = get_executed_sources(state)
```

### ✅ 2. Simplify Verbose Evidence Gathering

**Current:** 120+ lines of try/except blocks

**Proposed:** Use a tool registry pattern or agent-based approach:
```python
# Option A: Tool registry
EVIDENCE_TOOLS = [
    ("batch_stats", get_batch_statistics),
    ("failed_tools", get_failed_tools),
    ("failed_jobs", get_failed_jobs),
    ("error_logs", lambda tid: get_error_logs(tid, size=500, error_only=True)),
    ("all_logs", lambda tid: get_error_logs(tid, size=200, error_only=False)),
    ("host_metrics", get_host_metrics),
    ("airflow_metrics", get_airflow_metrics),
]

def gather_evidence_for_context(context: dict) -> dict:
    trace_id = _extract_trace_id(context)
    if not trace_id:
        return {}
    
    evidence = {}
    for key, tool_fn in EVIDENCE_TOOLS:
        try:
            result = tool_fn(trace_id) if callable(tool_fn) else tool_fn
            if isinstance(result, dict) and "error" not in result:
                evidence[key] = result
        except Exception:
            pass  # Tool failed, continue with others
    return evidence
```

**Option B:** Use LangGraph agent with tools for dynamic selection (better for future extensibility)

### ✅ 3. Standardize Evidence Format

**Create evidence schema:**
```python
# src/agent/evidence/schema.py
class EvidenceSchema(TypedDict):
    """Standardized evidence format."""
    # Context (metadata)
    tracer_web_run: dict
    pipeline_run: dict
    
    # Runtime evidence
    failed_jobs: list[dict]
    failed_tools: list[dict]
    error_logs: list[dict]
    all_logs: list[dict]
    host_metrics: dict
    airflow_metrics: dict
    
    # Source tracking
    evidence_sources_checked: list[str]
    evidence_sources_skipped: list[str]
```

### ✅ 4. Keep the Two-Step Flow (Architectural Decision)

**Current flow (KEEP THIS):**
```
hypothesis_execution (gather data) → diagnose_root_cause (interpret data)
```

**Why this is good:**
- ✅ Separation of concerns (data layer vs interpretation layer)
- ✅ Single responsibility per node
- ✅ Testable independently
- ✅ Reusable components
- ✅ Follows LangGraph best practices

**Do NOT merge gathering and analysis** - this would:
- ❌ Violate single responsibility principle
- ❌ Create tight coupling between API calls and analysis logic
- ❌ Make nodes less reusable
- ❌ Break the clean data → interpretation pipeline

## Metrics

- **Lines of actual duplicated code:** ~15-20 lines (source tracking logic)
- **Verbosity score:** High (120+ lines for evidence gathering)
- **Maintainability impact:** Medium (source tracking needs to be updated in 2+ places)
- **Architectural quality:** ✅ Good (proper separation of concerns maintained)

## Conclusion

### ✅ Actual Issues to Fix:

1. **Duplicate source tracking logic** - Extract to `get_executed_sources()` utility
2. **Verbose evidence gathering** - Use tool registry or agent pattern
3. **Inconsistent evidence formats** - Standardize schema
4. **Evidence source tracking inconsistency** - Unify tracking format

### ✅ Keep As-Is (Good Design):

1. **Two-step flow** - Gathering and analysis are different responsibilities
2. **Separation of concerns** - Data layer vs interpretation layer
3. **Node responsibilities** - Each node has a clear, single purpose

### Refactoring Impact:

- **Code reduction:** ~20-30 lines (removing duplication)
- **Maintainability:** Improved (single source of truth for source tracking)
- **Readability:** Improved (less verbose evidence gathering)
- **Architecture:** ✅ Maintained (no breaking changes to flow)

**Bottom line:** Fix actual duplications and verbosity, but **keep the architectural separation** - it's good design.
