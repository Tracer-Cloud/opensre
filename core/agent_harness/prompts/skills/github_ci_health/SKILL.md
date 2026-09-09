---
name: github-ci-health
description: >-
  Read-only GitHub CI health report of the checks failing right now for one
  repository, optionally narrowed to a branch or pull request. Not for CI/CD
  performance, reliability KPIs, failure rates, or downtime over a period
  (use cicd-analytics-demo).
metadata:
  owner: Tracer Team
  usecases:
    - Report the checks failing right now for one repository, branch, or pull request
    - Unattended recurring CI health report delivered to the shell inbox or a chat channel
    - Hand off a failing check to the interactive repair flow
  requires:
    - GitHub token usable by OpenSRE with read access to the repository
    - Configured repository owner and name (a schedule must supply both)
  type: report
  version: "1.1"
recurring: unattended
---

# GitHub CI health

Use this skill to report failing CI checks for exactly one configured GitHub
repository. The schedule must supply `owner` and `repo`; it may supply either
`branch` or `pr_number`, never both.

The scheduled runner supplies a pre-fetched CI health block. Treat that block
as the complete source of truth and return it faithfully as the final report;
do not discover a broader organization or repository scope. Preserve every
failing check, link, age, responsible PR or branch, coverage notice, and repair
handoff in the block.

## Tool usage

This skill does not issue GitHub tool calls during unattended execution. Before
the skill runs, the scheduled runner invokes `run_github_ci_health`, which
collects the configured repository's CI data through read-only GitHub REST GET
requests and supplies the complete rendered report.

Do not perform additional or fallback GitHub discovery. The only interactive
tool used by this skill is `propose_scheduled_delivery`, which configures the
recurring report and is never called during an unattended run.

This workflow is read-only. Never call `fix_github_pr_ci`, `github_cli`,
`shell_run`, or any other mutating or external-command tool during unattended
execution. Repairs must be requested interactively and explicitly approved.

When offering this report as a recurring task interactively, use kind
`recurring_skill` and skill name `github-ci-health`. Pass the exact `owner` and
`repo`, plus at most one of `branch` or `pr_number`, to
`propose_scheduled_delivery` so confirmation preserves the repository scope.

Interactively, the repository comes from the request; when it names none,
call `scan_local_git_workspace` and ask with `ask_user_choice`. Never run
`cli_exec` (`opensre health`, `opensre integrations …`), `shell_run`, or
`github_cli` to discover the repository, the token, or the environment: they
print unrelated integration state and are not part of this skill. To show
the checks failing right now during an interactive turn, use
`summarize_github_pr_status`; this skill's own report is produced by the
scheduled runner only.
