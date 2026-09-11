# Local CI Readiness — Mandatory Pre-Push Harness

This file is the **single source of truth** for required local validation before
any push or pull request. Repository-wide validation runs in GitHub Actions.
Feature- or package-specific validation required by an applicable contributor
guide supplements this harness and is intentionally not duplicated here.

<!--
Keep this document focused on required local checks and post-PR follow-through.
Do not add optional commands, CI implementation details, or tool-specific
procedures unless they change what contributors must do locally.
Automated contributors must not invent or run additional local CI steps beyond
the scoped checks below unless another applicable instruction or the user
explicitly requires them.
-->

## 0) Docs / process-only shortcut

If your diff is **only** documentation or contributor-process files, you may
skip the code-quality and test commands below.

Examples of files that qualify:

- `AGENTS.md`
- `CI.md`
- `CONTRIBUTING.md`
- `README.md`
- `docs/**/*.md`
- `docs/**/*.mdx`
- `docs/docs.json`

You may use the shortcut only when **all** changed files are non-runtime and
non-executable. If the diff touches application code, tests, build tooling,
dependency manifests, CI workflows, scripts, or anything with runtime impact,
run the normal harness.

For docs/process-only changes, the minimum required local check is:

```bash
git status --short
```

If you are unsure whether the shortcut applies, do **not** use it — run the
standard checks below.

## 1) Mandatory baseline checks (every code change that is not docs/process-only)

Run all of these first:

1. Clean working tree

   ```bash
   git status --short
   ```

   - No accidental untracked files
   - Never commit `.env` or secrets

2. Lint

   ```bash
   make lint
   ```

3. Format check

   ```bash
   make format-check
   ```

   If it fails:

   ```bash
   make format && make format-check
   ```

4. Typecheck

   ```bash
   make typecheck
   ```

## 2) Mandatory test harness (scope by touched modules)

Pick a focused test command for the modules you changed — do **not** default to
the full unit suite.

Map changed paths to targets using the `PathRule` entries in
[`.github/ci/test_scope_rules.py`](.github/ci/test_scope_rules.py):

- Rules with `always_escalate=True` identify high-blast-radius changes. Run the
  focused package and contract tests affected by the change.
- All other rules list a `test_targets` tuple — run those with
  `uv run python -m pytest <targets>`
- Changed files under `tests/` with no app rule run as-is

Use a focused `-k` filter when you only need a subset of a package.

## 3) Full suite runs in CI

The focused suite from section 2 is the required local test gate. Do not run
`make test-cov` as part of the normal local pre-push workflow; pull-request CI
runs the repository test suite in parallel shards.

List the focused tests you ran in the PR description. CI is the authoritative
repository-wide test result.

## 4) Pull-request latency and post-merge validation

The required automated pull-request execution gate has a p90 target of 90
seconds. Static checks, cached typechecking, duration-balanced pytest shards,
and interactive-shell checks run concurrently. Automated and
human review completion, including Greptile and Codex when available, remains a separate merge
requirement and is not part of that execution-time SLO.

Pull requests run the complete test selection without coverage instrumentation;
the same matrix produces and combines the full coverage report on `main`.

Full CodeQL `security-and-quality` analysis runs after every merge to `main` and
on the weekly schedule, not on ordinary pull requests. A production-only,
default-query profile is available through the CodeQL workflow's manual
`pr-fast` input for benchmarking. Do not make that profile required unless at
least ten representative runs demonstrate p90 at or below 75 seconds.

Post-merge validation is part of delivery. Monitor the `main` CI, CodeQL, and
release workflows for the merge commit; a failure requires an immediate fix or
revert and must not be reported as successful delivery.

## 8) Post-PR follow-through

Opening a pull request does not end the validation cycle. Follow it through until
the repository's merge requirements are satisfied: required GitHub checks are
green, actionable human or automated review feedback (including Greptile and
Codex when available) is
addressed, and resolved conversations are closed out.

Agents: the always-on rule lives in [AGENTS.md — CI failures and tests](AGENTS.md).
After every push, inspect `gh pr checks` / failing job logs and fix until required
jobs are green. The Cursor stop hook `.cursor/hooks/check-ci-failures.sh` will
re-prompt when the open PR still has failing checks.

A green check does not mean review feedback is clear. After checks complete,
and again after every push, inspect all unresolved conversations and latest
reviews. Validate each finding. For actionable feedback, push an appropriate
fix, reply, and resolve the addressed thread. For an incorrect or non-actionable
finding, reply with the rationale and resolve the thread without changing code.

After each completed PR update, once commits are pushed, the PR description is
current, and addressed threads are resolved, trigger the required automated
reviews. Follow [CONTRIBUTING.md](CONTRIBUTING.md#greptile-code-review) to
request Greptile; repeat until it reports 5/5 with no unresolved comments. If
Codex review is available for the repository, request it with `@codex review`
and address its actionable feedback. Do not re-trigger either reviewer while
its review is already running.

Use relevant built-in capabilities or locally installed skills, when available,
for PR monitoring, CI diagnosis, and review remediation rather than duplicating
tool-specific procedures in this document. Keep monitoring after each update;
do not treat creating or updating the PR as task completion. Validate review
suggestions before applying them, and rerun the appropriately scoped local
checks before pushing a fix.

## Precedence

If readiness instructions conflict across docs, **this file wins** for push/PR checks.
