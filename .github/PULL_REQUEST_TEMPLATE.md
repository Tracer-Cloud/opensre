## Fixes #<!-- issue number here -->

## Description

<!-- What changed and why? Keep it 2-3 sentences max.
Bad: "Updated the config parser"
Good: "Fixes #123: Config parser now handles nested YAML correctly. Changed recursive parsing from depth-first to breadth-first for better performance."
-->

---

## Type of Change

- [ ] 🐛 **Bug fix** (fixes #???, non-breaking)
- [ ] ✨ **Feature** (non-breaking)
- [ ] ⚠️ **Breaking change** (describe migration in "Impact" below)
- [ ] 📚 **Docs only**

**If breaking:** What must users change?

---

## Testing

**How was this tested?**
- [ ] Manual testing: <!-- specific steps (e.g., "Ran `opensre investigate --org myorg` with test data") -->
- [ ] Added tests: <!-- which tests cover the change? -->
- [ ] Updated existing tests: <!-- which ones? -->

**Test coverage:**
- [ ] New code has tests
- [ ] Edge cases covered (what edge cases did you test?)
- [ ] All checks pass locally:
  ```bash
  make lint && make typecheck && make test-cov
  ```

**Before/After evidence:**
<!-- If visible changes: include CLI output, logs, or behavior differences -->

---

## Code Quality Review

**Self-review completed?**
- [ ] Read through my own code once before requesting review
- [ ] Checked for obvious bugs, typos, or logic errors
- [ ] Removed debug statements and commented-out code
- [ ] Variable names are clear (no single-letter vars except loop counters)

**Explainability:**
- [ ] I can explain **why** each function exists
- [ ] I can explain **what** each function does
- [ ] I understand the control flow (not just copy-pasted code)

---

## Code Understanding & AI Usage

**Did you use AI tools (ChatGPT, Claude, Copilot, GitHub Copilot)?**
- [ ] No, I wrote all the code myself
- [ ] Yes, I used AI assistance (check all below)

**If AI-assisted, confirm you:**
- [ ] Reviewed **every single line** of AI-generated code (not just skimmed)
- [ ] Understand the logic and can explain it in your own words
- [ ] Tested edge cases (what could break?)
- [ ] Modified output to match project conventions ([CONTRIBUTING.md](../CONTRIBUTING.md))
- [ ] Verified tests pass with the AI-generated code

---

## Impact Analysis

- **Backward compatible?** Yes / No (if no: describe migration)
- **Breaking changes?** None / (describe what breaks and how to migrate)
- **Performance impact?** None / (describe impact and measurements)
- **New dependencies?** No / (list and justify)
- **Database migrations?** No / (describe changes)

---

## Checklist Before Requesting Review

- [ ] Branch name follows convention: `issue/123-description` or `fix/description`
- [ ] PR title is descriptive and linked to issue (e.g., "Fixes #123: Add export feature")
- [ ] **All required checks pass:** `make lint && make typecheck && make test-cov`
- [ ] No unrelated changes mixed in (one PR = one concern)
- [ ] No debug code, console.logs, or commented-out sections
- [ ] Code style matches project conventions
- [ ] Tests added/updated (or documented why not applicable)
- [ ] Documentation updated (if behavior changed)

---

**How to make this PR easier to review:**
1. Keep it focused (one feature/fix per PR)
2. Include test evidence (test names, coverage %)
3. Highlight tricky sections ("pay attention to line 42")
4. Link related issues

**Allow edits from maintainers?** ☑️ Check if you'd like help with minor adjustments.
