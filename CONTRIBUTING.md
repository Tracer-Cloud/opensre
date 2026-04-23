# Contributing

Thanks for your interest in contributing to OpenSRE.

This document describes how to set up your environment, propose changes, report bugs, and submit pull requests in a way that keeps review fast and the project reliable.

## Quick Links

- **Docs:** https://tracer.mintlify.app
- **Support / Contact:** hello@tracer.cloud
- **Security Issues:** See `SECURITY.md` (do not open public issues)

## Before You Start

### How to Contribute

There are two ways to get started:

#### Path A: Pick an Existing Issue

1. Browse the [open issues](../../issues) on GitHub
2. Find one that interests you and comment to **request assignment**
3. Once assigned, fork the repo, create a branch, and start working
4. Open a PR when ready

#### Path B: Find Something Yourself

1. Explore the codebase or use the product — find a bug, improvement, or missing feature
2. Open a new GitHub Issue describing what you found (use the appropriate template below)
3. Comment that you're working on it and open a **draft PR** early so others can see progress
4. Mark the PR as ready for review when it's complete

#### Issue Assignment & Contribution Flow (Path A)

- Only work on issues assigned to you
- Or comment with an approach and wait for confirmation before starting
- Avoid picking up already assigned issues
- The first contributor to comment with a clear approach will be assigned the issue

#### Issue Templates

- **Bugs & small fixes:** [Bug report template](.github/ISSUE_TEMPLATE/bug_report.md)
- **New features / behavioral changes:** [Feature request template](.github/ISSUE_TEMPLATE/feature_request.md) — discuss the approach before coding
- **Improvements / refactoring:** [Improvement template](.github/ISSUE_TEMPLATE/improvement.md)
- **Questions / "how do I":** Use the docs or email hello@tracer.cloud (Issues are for actionable work)
- **Security issues:** Follow `SECURITY.md` — do not open a public issue

### Environment Setup

See **[SETUP.md](SETUP.md)** for detailed setup instructions including Windows-specific guidance.

**Quick start:**

1. Clone the repo and create a virtual environment
2. Install dependencies: `pip install -e ".[dev]"`
3. Run checks: `make lint && make format-check && make typecheck && make test-cov`
4. Build release artifacts when needed: `make build`

If you prefer VS Code, you can use the repo's devcontainer at [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json) instead of setting up Python manually.

---

**The full flow:**

1. **Find or create an issue** — Pick an existing one (Path A) or raise a new one (Path B)
2. **Request assignment** — Comment on the issue so maintainers know you're working on it
3. **Discuss (if needed)** — For features/changes, discuss approach in the issue before coding
4. **Fork and branch** — Create a branch for your work: `git checkout -b issue/123-description`
5. **Code and test** — Make changes, add tests, ensure all checks pass
6. **Submit a PR** — Open a pull request (or draft PR) linked to the issue; use the PR template
7. **Review & iterate** — Respond to feedback, make changes as needed
8. **Merge** — Maintainer merges once approved

**Detailed steps:** See "Development Workflow" section below.

---

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b issue/123-short-description
```

Use `issue/` or `fix/` prefix. Branch names should be lowercase with hyphens.

### 2. Make Changes

- Keep commits focused and logical
- Write clear commit messages: `"Fix: CLI returns error on incomplete commands"`
- One concern per commit when possible

### 2.1 Add a Tool (Fast Path: Single File)

For simple tools, you do not need a class or `ClassVar` metadata. Add one file under `app/tools/` and register a function with `@tool`.

Example (`app/tools/example_status_tool.py`):

```python
from app.tools.tool_decorator import tool


@tool(source="knowledge")
def get_example_status(run_id: str, include_history: bool = False) -> dict[str, object]:
    """Return a lightweight status summary for a run."""
    return {
        "run_id": run_id,
        "include_history": include_history,
    }
```

Notes:

- `source` is required for function tools.
- `name`, `description`, and `input_schema` are inferred by default.
- `surfaces` defaults to `("investigation",)`. Pass `surfaces=("investigation", "chat")` to expose the tool in both investigation and chat contexts.
- Use the existing package/class style when a tool has complex helper logic, multiple exports, or substantial integration-specific code.

### 3. Add or Update Tests

- Bug fixes should include a test that would have caught the bug
- New features should have corresponding tests
- Aim for >80% code coverage (run `make test-cov` to check)

### 4. Run Local Checks (Required Before PR)

```bash
make lint          # ruff: check code style
make format-check  # ruff: check formatting (read-only)
make typecheck     # mypy: check type annotations
make test-cov      # pytest: run tests with coverage report
```

All four must pass. **CI will block merging if any fail.**

### 5. Open a Pull Request

Follow the PR template (see below). Link the relevant issue and describe what changed and why.

## Pull Request Guidelines

### How to Write a Good PR Description

Use the **[PR template](.github/PULL_REQUEST_TEMPLATE.md)** (automatically provided when you open a PR). Key sections:

- **Issue link:** `Fixes #123` (auto-closes the issue when merged)
- **Type of Change:** Select bug fix, feature, breaking change, or docs (helps categorize)
- **Description:** What changed and why
- **Testing:** How you tested it with specific steps and evidence
- **Impact Analysis:** Is it backward compatible? Any breaking changes? Performance impact?

### PR Checklist Before Submitting

- [ ] Linked to the relevant issue
- [ ] All local checks pass: `make lint && make format-check && make typecheck && make test-cov`
- [ ] Added tests for bug fixes or new features
- [ ] Updated documentation if behavior changed
- [ ] Code follows project style (see **Code Quality** section below)
- [ ] Self-reviewed your own code first
- [ ] Considered edge cases

### If Your PR Includes Screenshots or Logs

Provide **before** and **after** examples when:

- Changing CLI output or error messages
- Updating agent behavior
- Fixing a bug with visible impact

### AI-Assisted PRs

If you used AI tools (Claude, ChatGPT, Copilot, etc.) to generate code, the **[PR template](.github/PULL_REQUEST_TEMPLATE.md)** requires you to confirm:

- [ ] I reviewed **every single line** of AI-generated code (not just skimmed)
- [ ] I understand the logic and can explain it in my own words
- [ ] I tested edge cases (what could break?)
- [ ] I modified output to match project conventions ([Code Quality Standards](#code-quality-standards))
- [ ] Verified tests pass with the AI-generated code

This ensures you understand the code, not just copied it. Reviewers will pay extra attention to AI-assisted code.

## Code Quality Standards

- **Clarity over cleverness:** Code should be easy to understand and maintain
- **DRY principle:** Don't repeat yourself; extract common patterns
- **Strong typing:** Use type hints for all function parameters and returns
- **One responsibility:** Each function/class should do one thing well
- **Comments for "why":** Explain non-obvious logic; code already shows the "what"
- **Breaking changes:** Call them out explicitly in PR descriptions and docs

### Style & Formatting

We use:

- **ruff** for linting and import sorting
- **mypy** for strict type checking
- **Black-compatible** formatting (4-space indents)
- **pytest** for testing with coverage tracking

Run these before every commit:

```bash
make lint          # Auto-fixes many style issues
make format-check  # Checks formatting without modifying files
make typecheck     # Catches type errors
make test-cov      # Ensures tests pass and coverage is tracked
```

To verify the package can be shipped, run:

```bash
make build
```

## Reporting Bugs

Use the **[bug report template](.github/ISSUE_TEMPLATE/bug_report.md)** when creating an issue. It guides you to include:

- **Summary:** One-line description of the bug (specific, not vague)
- **Expected behavior:** What should happen
- **Actual behavior:** What actually happens (with error message)
- **Reproduction steps:** Clear, minimal steps to consistently trigger the bug
- **Can you reproduce it consistently?** Every time / Intermittent / Sometimes
- **Environment:** OS, Python version, agent version, install method, relevant config
- **Error output:** Full error messages and logs (redact secrets like API keys)
- **Workarounds:** If you found a way to work around it
- **Context:** What were you trying to do? Is this blocking your work?

**Example:**

```
### Expected Behavior
`opensre investigate --org myorg` should return investigation results

### Actual Behavior
Command exits silently with no output
Error: exit code 0

### Steps to Reproduce
1. Run `opensre investigate --org myorg`
2. Observe output

### Environment
- OS: macOS 14.2
- Python: 3.11.5
- opensre version: v0.2.1
```

## Requesting Features

Use the **[feature request template](.github/ISSUE_TEMPLATE/feature_request.md)** to propose new functionality. It guides you to clarify:

- **Problem statement:** Why do we need this? (focus on the problem, not solution)
- **Proposed solution:** How should it work? (specific and concrete with examples)
- **Acceptance criteria:** What needs to be true for this to be "done"?
- **Alternative approaches:** Other solutions you considered and why you prefer this one
- **Backward compatible?** Yes / No / Breaking changes (describe what changes)
- **Impact:** Which modules? New dependencies?

## Suggesting Improvements

Use the **[improvement template](.github/ISSUE_TEMPLATE/improvement.md)** to propose refactors, optimizations, or quality improvements. It requires:

- **Current state:** How does it work now? (with code references)
- **Desired state:** How should it work instead?
- **Why it matters:** Performance? Maintainability? Reliability?
- **Scope:** One focused concern per issue (not bundled work)
- **Acceptance criteria:** How will we measure success?
- **Metrics:** Before and after values (e.g., "15ms → <1ms")

## Need Help?

- **Setup issues?** Check this guide first, then open an issue with details
- **How do I...?** Check the project docs or ask in a Discussion
- **Found a bug?** Open a bug report issue with the template
- **Have an idea?** Start a Discussion to gauge interest before opening an issue

## Licensing

By contributing, you agree that your contributions will be licensed under the project's license (see `LICENSE`).
