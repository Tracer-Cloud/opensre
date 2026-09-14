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

## 0) Setup and automatic push validation

Run `make install` after cloning. It installs locked development dependencies
and a blocking pre-push hook for this checkout. For an existing environment,
run `make install-hooks`. Installation preserves existing hooks and keeps
linked worktrees independent.

The hook validates the **committed revisions being pushed** in temporary Git
worktrees with their locked dependencies. An uncommitted fix cannot make a
broken commit pass. Existing push hooks run first and receive Git's original
arguments and ref updates.

## 1) Mandatory local validation

Before committing, run:

```bash
make pre-push
```

This checks the working tree, including untracked files. It runs lint,
formatting, types, strict import boundaries, integration/tool registries,
repository-wide contracts, and tests selected from the diff. Independent
checks run concurrently and all failures are reported in one run.

Normal checks target **60 seconds**. Dependency preparation is separate;
cold caches and broad changes can take longer. Required checks finish even
when the target is exceeded. Failure blocks the push.

Inspect the selection without running it:

```bash
make pre-push ARGS=--dry-run
```

The default comparison uses the remote default branch, falling back to
`origin/main`. Without a remote base, all tracked files are considered.
Override it when needed:

```bash
make pre-push ARGS='--base origin/release'
```

A missing explicit base is an error. Fetch that branch and retry. Unknown
source paths or stale test targets also block validation: update the mapping
in [`.github/ci/test_scope_rules.py`](.github/ci/test_scope_rules.py).

Documentation-only diffs skip code checks. Runtime prompts, scripts,
workflows, and dependency changes do not qualify for that shortcut.

## 2) Focused tests and complete CI

Use `make test-scope` to run only the affected tests during development. It
uses the same mapping as the push gate and never falls back to a full coverage
run. Package-specific validation required by contributor guides still applies.

GitHub Actions uses the same quality check definitions as the local gate and
runs the complete test matrix. The local gate does not replace repository-wide
CI, Linux/Windows checks, CodeQL, packaging, or release validation. List the
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
