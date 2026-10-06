---
name: fixing-github-merge-conflicts
description: >-
  Merge the base branch into open pull requests that conflict with it, one at a
  time with fix_github_pr_ci, and report the merges a person must decide. Use
  when asked to fix the merge conflicts on a repository's pull requests, and for
  scheduled conflict loops.
metadata:
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-10-04
  usecases:
  - For a scheduled loop that keeps a repository's open pull requests free of merge conflicts.
  - For maintainers asking OpenSRE to fix the merge conflicts on a repository's open pull requests.
  requires:
  - GitHub authentication with write access to the pull request branches.
  - An installed and authenticated coding agent for conflicts git cannot merge on its own.
  - The summarize_github_pr_status and fix_github_pr_ci tools.
  version: "1.1"
---

# Fixing GitHub merge conflicts

Merge the base branch into each open, same-repository, non-draft pull request
that conflicts with it.

## Plan

Only when step 1 finds a target, list `update_plan` before the first
`fix_github_pr_ci` call: step 1 `completed`, step 2 `in_progress` with
`verifies: true`, step 3 pending.

- [ ] Step 1. Find the conflicting pull requests with summarize_github_pr_status.
- [ ] Step 2. Merge the base branch into each one with fix_github_pr_ci.
- [ ] Step 3. Reply with one line per new result.

## Workflow

### 1. Find the conflicting pull requests

Call `summarize_github_pr_status(owner, repo, conflicts_only=true)`; the targets
have `repairable: true` and either `mergeable: false` or `mergeable_state: "dirty"`.
Complete when the scan has returned.

### 2. Merge the base branch into each target

Call `fix_github_pr_ci(owner, repo, pr_number)` once per target, one at a time;
it merges, pushes, waits for the checks, and comments on the pull request when
a person must decide. Never run `git` or `gh` around it, and never merge a pull
request. Complete when every target has a result.

### 3. Reply

Write one line per target whose result is new (one with `already_reported: true`
is not): its link, then `checks_state` and any `error_kind` and `error`, as
returned; with no line left, reply with only the note line. Complete when the
reply is sent.
