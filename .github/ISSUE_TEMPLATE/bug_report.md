---
name: Bug Report
about: Report something that isn't working
title: "[BUG] "
labels: bug
---

## Summary

<!-- One-line description of the bug. Be specific.
Example: "Agent fails to parse config when .env vars contain special characters"
NOT: "Something doesn't work" -->

---

## Expected Behavior

<!-- What should happen? Be concrete.
Example: "Agent should successfully parse the config and return no errors"
Include relevant output/logs showing expected state. -->

## Actual Behavior

<!-- What actually happens instead? Include the exact error output.
Example: "Agent exits with ValueError: invalid syntax" -->

---

## How to Reproduce

**Minimal steps to consistently trigger the bug:**

1. <!-- First step - be specific (not just "run the app", but "run: opensre investigate --org myorg") -->
2. <!-- Second step -->
3. <!-- Expected failure point -->

**Can you reproduce it consistently?** Yes / No / Sometimes

**How often does it occur?** Every time / Intermittent / Under specific conditions

---

## Environment

- **OS:** macOS 14.2 / Ubuntu 22.04 / Windows 11 (be specific)
- **Python:** `python --version` (paste output)
- **Agent version:** Specific version tag OR git commit hash (`git rev-parse HEAD`)
- **Install method:** pip / from source / Docker
- **Relevant config:** Any .env vars, opensre.json settings, or special setup

---

## Logs & Error Output

<!-- Paste the full error message and relevant logs. Redact secrets (API keys, tokens, passwords). -->

**Error Message:**
```
[paste exact error here]
```

**Full logs (if helpful):**
```
[paste relevant logs here]
```

---

## Workarounds

<!-- If you found a way to work around the bug, describe it here. Helps others and shows scope. -->

None found / Already tried X but Y happened

---

## Additional Context

<!-- Anything else? Screenshots, related issues, attempted fixes? -->

**Related issues:** Links to similar issues if any
**Context:** What were you trying to do when you hit this?
