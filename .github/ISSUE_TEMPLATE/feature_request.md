---
name: Feature Request
about: Suggest a new feature or capability
title: "[FEATURE] "
labels: enhancement
---

## Problem Statement

<!-- **Why** do we need this feature? Describe the user pain point or limitation.
Examples:
- "Users can't export agent logs to external systems"
- "No way to specify custom alert rules without code changes"
Focus on the problem, not the solution. -->

---

## Proposed Solution

<!-- **How** should this feature work? Be specific and concrete.
Include:
- What new commands/APIs would exist?
- How would users interact with it?
- What data/inputs/outputs?

Example:
```
opensre export --format json --output alerts.json
```

This would export all alerts to a JSON file with structure:
```json
{
  "alerts": [...],
  "metadata": {...}
}
```
-->

---

## Acceptance Criteria

**Must have (required for "done"):**
- [ ] [Specific, measurable outcome - e.g., "CLI accepts --export flag"]
- [ ] [Specific, measurable outcome - e.g., "JSON output validates against schema"]
- [ ] [Specific, measurable outcome - e.g., "Tests cover happy path and error cases"]

**Nice to have (if time permits):**
- [ ] [Optional enhancement - e.g., "CSV export support"]

---

## Alternative Approaches

<!-- Did you consider other solutions? List them and explain why you prefer your proposal.
This shows you've thought it through and makes review faster.

Example:
- **Option A:** Use environment variable for config - Rejected because users want per-command control
- **Option B:** Extend existing API - Good but requires breaking changes
- **Option C (preferred):** New CLI flag - Backward compatible, intuitive
-->

---

## Impact & Scope

- **Backward compatible?** Yes / No / Breaking changes: (describe)
- **Affects modules:** agent-core / CLI / config / storage (which parts change?)
- **New dependencies?** None / (list new libraries if any)

---

## Additional Context

<!-- Links, related issues, user requests, design docs, etc. -->
- Related to: #123
- Similar in: [other project/tool]
- Discussed in: [link to discussion]
