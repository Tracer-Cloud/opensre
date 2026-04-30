# Scenario 011 — CPU + Storage Compositional Fault

## Overview

| Field | Value |
|------|------|
| Instance | `analytics-prod` |
| Fault Type | Compositional (CPU + Storage) |
| Root Cause Category | `resource_exhaustion` |

---

## Expected Behaviour

The agent should identify **two independent root causes**:

1. CPU saturation driven by an analytics aggregation query
2. Storage exhaustion driven by an `audit_log` INSERT workload

---

## Reasoning Expectations

### 1. Dual Root Cause Identification
The agent should:
- Identify two independent workloads
- Recognize two independent resource limits (CPU + storage)

---

### 2. audit_log Attribution
The agent should:
- Explicitly mention `audit_log` when storage exhaustion is driven by audit logging
- Avoid replacing it with generic terms (e.g. logs, ingestion, temporary tables)

---

### 3. Connection Growth Interpretation
The agent should:
- Treat connection growth as a symptom of blocked writers or backpressure
- Avoid diagnosing `connection_exhaustion` without direct evidence of max_connections being reached

---

### 4. ReplicaLag Interpretation
If replication lag signals (e.g. ReplicaLag, WAL pressure, or replication delay indicators) are present or strongly implied:
- ReplicaLag should be described as a downstream symptom of the write/WAL burst
- It should not be treated as an independent root cause

If such signals are not present:
- ReplicaLag should not be mentioned

---

### 5. Independence Constraint
The agent should:
- Explicitly describe the two faults as independent
- Recognize that their timing may be coincidental

---

## Common Failure Modes

- Only identifying CPU and missing storage exhaustion
- Diagnosing `connection_exhaustion` from connection spikes caused by blocked writers
- Treating ReplicaLag as a separate or independent fault
- Merging both faults into a single blended root cause
- Using generic storage explanations instead of `audit_log`

---

## Validation Notes

This scenario validates:
- compositional fault reasoning
- separation of independent workloads
- correct classification of symptoms vs root causes
- grounding explanations in available evidence
