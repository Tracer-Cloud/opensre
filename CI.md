# Local Feedback and Required PR Validation

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

## 0) Setup and automatic push validation

Run `make install` after cloning. It installs locked development dependencies
and a blocking pre-push hook for this checkout. For an existing environment,
run `make install-hooks`. Installation preserves existing hooks and keeps
linked worktrees independent.

The hook runs a broader PR-readiness gate when a branch is first published to a
remote: locked dependencies, all shared quality checks, and tests selected from
the branch diff. Later pushes keep feedback fast by running only Ruff lint and
formatting checks on changed Python files. Temporary Git worktrees ensure an
uncommitted source fix cannot hide a committed failure. Like other local Git
hooks, the validation implementation is trusted local tooling, not a tamper-proof
security boundary. Required PR CI remains authoritative. Existing push hooks
still run first with Git's original arguments and ref updates.

If the remote has no available default-branch base, the first-push gate runs all
shared quality checks without diff-selected tests rather than treating every
tracked file as part of the branch change.

Run `make pre-push` (or `make check`) for the same fast checks on your working
changes. Use `ARGS='--base upstream/main'` to select a base explicitly. Without
an available remote base, all tracked Python files are checked. Deleted files
are ignored; configuration-only changes are validated by PR CI.

Run `make pr-ready` explicitly before opening a pull request from a branch that
already exists on the remote. The automatic broader gate runs again if a deleted
remote branch is recreated; it does not run for tags.

## 2) Focused tests and complete CI

Run focused regression tests while developing changed behavior. Use
`uv run python -m pytest <test-path>` for a narrow selection or `make test-scope`
for the affected package suites. The latter can select thousands of tests; it
is not a mandatory pre-push step. Do not repeat a passing suite solely to push.
Package-specific validation required by contributor guides still applies.

For an explicit full local validation run, use `make check-full`: all shared
quality checks followed by the full test suite. This is opt-in, not a routine
commit, push, or PR prerequisite.

GitHub Actions runs all shared quality checks and the complete test matrix.
The local gate does not replace repository-wide CI, Linux/Windows checks, CodeQL, packaging, or release validation. List the
focused tests you ran in the PR description.

## 3) Emergency override

For an intentional emergency bypass, supply a reason for that push only:

```bash
git -c opensre.prePushOverride='incident reference and reason' push
```

The hook prints the override and appends the reason, timestamp, and pushed
revisions to `pre-push-overrides.jsonl` inside this checkout's Git directory.
Existing user hooks still run. This bypass does not waive remote CI or merge
requirements. Do not set the override permanently in Git configuration.

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

After every push to an open PR, run `gh pr checks --watch` or inspect
`gh pr view --json statusCheckRollup,url`. On failure, retrieve the job log with
`gh run view <id> --log-failed`, fix the root cause in product or test code,
rerun focused checks for the touched modules, and push. Continue until required
jobs are green (or skipped where appropriate); do not skip tests or use
constant-condition toggles to hide failures.

Tests that fail under CI load are real bugs: harden synchronization rather than
ignoring flakes. For import/API-border failures, use the package API allowed by
`.importlinter.strict` or the border allowlist, not an internal leaf unless the
edge is explicitly exempted.

The Cursor stop hook `.cursor/hooks/check-ci-failures.sh` re-prompts when an open
PR still has failing checks. Treat that follow-up as blocking work, not a suggestion.

A green check does not mean review feedback is clear. After checks complete,
and again after every push, inspect all unresolved conversations and latest
reviews. Validate each finding. For actionable feedback, push an appropriate
fix, reply, and resolve the addressed thread. For an incorrect or non-actionable
finding, reply with the rationale and resolve the thread without changing code.

After each completed PR update, once commits are pushed, the PR description is
current, and addressed threads are resolved, trigger the required automated
reviews. Request Greptile with `@greptile review`; repeat until it reports 5/5
with no unresolved comments. Reviews commonly take 5–10 minutes or longer. If
Codex review is available for the repository, request it with `@codex review`
and address its actionable feedback. Do not re-trigger either reviewer while
its review is already running.

When available, use the [greploop skill](https://skills.sh/greptileai/skills/greploop)
to manage this review loop.

Use relevant built-in capabilities or locally installed skills, when available,
for PR monitoring, CI diagnosis, and review remediation rather than duplicating
tool-specific procedures in this document. Keep monitoring after each update;
do not treat creating or updating the PR as task completion. Validate review
suggestions before applying them, and rerun the appropriately scoped local
checks before pushing a fix.

## Precedence

If readiness instructions conflict across docs, **this file wins** for push/PR checks.
