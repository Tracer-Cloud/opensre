# Scenario 006 — Replication Lag with CPU Red Herring

## Overview

| Field | Value |
|---|---|
| Instance | `analytics-prod` |
| Failure Mode | Replication Lag |
| Confounder | CPUUtilization (analytics SELECT) |
| Severity | Critical |

This scenario validates whether the agent can correctly identify a **single root cause** in the presence of a strong but misleading signal.

---

## Ground Truth

- Root Cause: Replication lag caused by WAL generation from a write-heavy batch UPDATE
- Root Cause Category: resource_exhaustion
- CPUUtilization: **Red herring** caused by an unrelated analytics SELECT query

The two workloads are **causally independent**.

---

## Expected Behaviour

A correct agent must:

- Identify replication lag as the **only root cause**
- Explain the **WAL mechanism** (write-heavy UPDATE → WAL generation → replica lag)
- Explicitly state that CPU is:
  - unrelated
  - a red herring / confounder
  - **not the root cause**
- Separate the two concurrent workloads:
  - UPDATE → WAL → replication lag
  - SELECT → CPU usage (independent)

---

## Required Reasoning Elements

The response should include:

- Explicit mention of **WAL**
- Recognition of **replication lag dynamics**
- Clear statement that CPU is:
  - unrelated
  - not causally linked
- Identification of **two independent workloads**, but **only one root cause**

---

## Strict Validation Rules

The response must:

- Contain language indicating CPU is **not the root cause**
- Treat replication lag as the **single root cause**
- Avoid blending CPU into the causal chain

---

## Failure Modes

The scenario must FAIL if the agent:

- Identifies CPU as the root cause
- Diagnoses `cpu_saturation`
- Describes CPU as:
  - a second root cause
  - an independent root cause
  - a contributing factor
- Produces statements such as:
  - "two root causes"
  - "multiple root causes"
- Blends UPDATE and SELECT into one causal mechanism
- Fails to mention WAL

---

## Passing Criteria

A correct response:

- Attributes the issue solely to WAL-driven replication lag
- Clearly separates the analytics SELECT from the root cause
- Explicitly rejects CPU as causal
- Demonstrates mechanism-level reasoning rather than surface correlation

---

## Reviewer Notes

This scenario is intentionally adversarial.

A naive agent may incorrectly:

- correlate CPU spikes with replication lag
- treat concurrent signals as causally linked
- produce multi-root-cause explanations

A correct agent must demonstrate **causal discrimination**, not correlation.

This validation ensures the agent can:

- ignore strong but misleading signals
- isolate the true mechanism
- produce a clean, single-root-cause RCA
