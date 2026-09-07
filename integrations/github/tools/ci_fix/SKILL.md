---
name: github-ci-fix
description: Use when the user asks OpenSRE to fix failing GitHub CI, GitHub Actions checks, failing pull request checks, a broken PR branch, or CI on a named branch such as main.
tools:
  - fix_github_pr_ci
---

# GitHub CI Fix

Use `fix_github_pr_ci` for GitHub CI remediation requests, not
`github_cli` or `shell_run`.

Rules:

- Pass `pr_url` when the user provides a GitHub pull request URL.
- Pass `owner`, `repo`, and `pr_number` when the user names a repo and PR number.
- Pass `branch` (e.g. `branch="main"`) when the user asks to fix a branch's
  failing CI itself; never combine it with a PR selector. Merged or closed PRs
  are refused — use `branch` for failures already on the base branch.
- If no repo is named, omit `owner` and `repo`; the tool uses the current
  checkout's GitHub origin.
- The tool inspects failing GitHub Actions checks, fixes the local checkout or a
  temporary linked git worktree, commits, pushes the PR branch or fresh repair
  branch, and waits for the checks triggered by that push. It does not open a
  new PR.
- Fork PR branches are refused by the tool because OpenSRE only pushes to
  branches in the same repository.
- Branch targets such as `main` are never pushed directly; OpenSRE pushes a
  fresh `opensre/ci-fix-*` repair branch.
- When GitHub reports the PR as conflicted with its base branch (checks never
  start), the tool merges the base branch into the PR branch first, resolves
  the conflicts, regenerates lockfiles, and pushes. A conflict it cannot resolve
  safely is reported with the exact files and what a person must decide; the
  merge is aborted and nothing is pushed. Do not run `git merge` around it.
- The tool owns CI log inspection, fix execution, branch checkout, commit, and
  push plus post-push check verification. Do not run a raw `gh` workflow around it.
- If the tool returns `response_text`, output exactly that text and stop.
- If no fix is produced, keep the reply to one short line from `error`; do not
  say "next steps", add numbered options, list example commands, or ask a broad
  follow-up question.
