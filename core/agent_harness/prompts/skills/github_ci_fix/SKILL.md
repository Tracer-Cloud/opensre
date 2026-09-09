---
name: github-ci-fix
description: >-
  Fix failing GitHub CI / Actions checks via fix_github_pr_ci and push to the
  existing PR head, or fix a branch's failing CI via a linked repair worktree
metadata:
  owner: Tracer Team
  usecases:
    - Fix failing CI checks on a pull request and push to its head branch
    - Fix failing CI on a branch such as main through a linked repair worktree
    - Repair the checks of a pull request given by URL or owner/repo#number
  requires:
    - GitHub account with write access to the target repository
    - Local checkout whose origin matches the target repository
    - GitHub token usable by OpenSRE
    - Installed and authenticated coding agent
  type: repair
  version: "1.0"
---

# GitHub CI fix

Use `fix_github_pr_ci` to repair failing checks on a pull request or a named
branch.

## When to use

- The user asks to fix failing CI, broken GitHub Actions checks, failing PR
  checks, a red pull request branch, or CI on a named branch such as `main`.
- The user says "fix CI on this PR", "fix the CI of PR 123 and push", "repair
  the failing checks on owner/repo#123", or provides a GitHub pull request URL
  and asks for CI/check fixes.
- The user asks to fix failing CI on a branch itself — "fix the CI on main",
  "main is red, fix it", or "fix CI on the default branch and push" — with no
  PR involved.

## Related workflows

- Ordinary PR reads, comments, closes, merges, labels, or issue work. Use
  `github_cli`.
- Security alert remediation. Use `fix_github_security_alert`.

## Workflow rules

- For a GitHub PR URL, call:
  `fix_github_pr_ci(pr_url="<url>")`
- For `owner/repo#123` or "PR 123 in owner/repo", call:
  `fix_github_pr_ci(owner="owner", repo="repo", pr_number=123)`
- For "fix the CI on main" (or any named branch with no PR), call:
  `fix_github_pr_ci(branch="main")`
  Never pass `branch` together with a PR selector, and never invent a branch
  the user did not name.
- If no owner/repo is named, omit both and let the tool use the current
  checkout's GitHub origin.
- Never use `github_cli` or `shell_run` to run raw `gh pr checks`, `gh run view`,
  checkout, commit, or push for this workflow. The CI fixer owns PR metadata,
  failing-check log inspection, fix execution, branch safety, commit, and push.
- The tool pushes to the existing PR head branch after approval. Do not ask the
  user whether to open a new PR.
- A PR that conflicts with its base branch (GitHub shows it as not mergeable
  and starts no checks) is handled by the tool: it merges the base branch into
  the PR branch, resolves conflicts, regenerates lockfiles, pushes, and waits
  for the checks. Never run `git merge`, `git rebase`, or conflict edits around
  it. If it reports blocked files, relay that one line and stop.
- For branch targets such as `main`, the tool creates a separate linked git
  worktree, commits on a fresh `opensre/ci-fix-*` branch, and pushes that branch.
  It never pushes directly to protected branches.
- If the tool returns `response_text`, output exactly that text and stop.
- If `error_kind` is set, reply in one short line from `error`. Do not say
  "next steps", do not add numbered options, do not list example commands, and
  do not ask a broad follow-up question.

## Examples

- "fix CI on https://github.com/Tracer-Cloud/opensre/pull/4597 and push"

  ```text
  fix_github_pr_ci(pr_url="https://github.com/Tracer-Cloud/opensre/pull/4597")
  ```

- "fix failing checks on Tracer-Cloud/opensre#4597"

  ```text
  fix_github_pr_ci(owner="Tracer-Cloud", repo="opensre", pr_number=4597)
  ```

- "fix CI on main in Tracer-Cloud/opensre and push"

  ```text
  fix_github_pr_ci(owner="Tracer-Cloud", repo="opensre", branch="main")
  ```

- "the current PR CI is failing, fix and push"

  ```text
  fix_github_pr_ci()
  ```

- "fix the CI on main"

  ```text
  fix_github_pr_ci(branch="main")
  ```
