---
name: operating-github-cli
description: >
  Default GitHub skill for the action agent. Use github_cli for any GitHub
  request — create/list/view/close issues and PRs, assign, labels, repos, releases,
  checks, github.com/owner/repo URLs, or gh api. Prefer over shell_run/!gh.
  Run these with github_cli in the current agent turn.
tools:
  - github_cli
---

# operating-github-cli

Authenticated `gh` for OpenSRE. Reads and writes — no approval gate. Prefer over
`shell_run` / `!gh`. Pass `args` after `gh`; optional `repo` as `owner/name` → `-R`.
Blocked top-level: `auth`, `extension`, `workflow`, `run`, `secret`,
`codespace`, `ssh-key`, `gpg-key`, `config`.

## Capabilities

| Intent | Example `args` |
| --- | --- |
| Create issue | `["issue", "create", "--title", "…", "--body", "…"]` |
| List / view issues | `["issue", "list"]` / `["issue", "view", "42"]` |
| Close / comment / edit | `["issue", "close", "42"]` / `["issue", "comment", "42", "--body", "…"]` |
| List / view / close / merge PRs | `["pr", "list"]` / `["pr", "view", "45"]` / `["pr", "close", "45"]` / `["pr", "merge", "45"]` |
| Repos / search | `["repo", "list"]` / `["search", "issues", "crash"]` |
| Arbitrary API | `["api", "repos/OWNER/REPO/issues"]` |

## After github_cli returns

Use `summary` when present. Reply short and chat-like.

- **Simple** (create/close/comment/merge, URL/`#n`): plain prose, one sentence.
- **Structured reads**: light chat markdown — short lead-in + bullets
  (`* #42 — title`); no tables/headers.
- **Mutate extras:** at most 2–4 bullets. No GraphQL/JSON dumps. No "I found:".
- **Failure:** one sentence from `error` / `error_type` — say it failed to run.

## Prefer dedicated tools when they clearly fit

Slack propose/execute; workflow digests; investigation code/commit search.
PR CI fix-and-push → fix_github_pr_ci. Security/quality alerts →
fix_github_security_alert.

## Limitations

`gh` on PATH; OpenSRE token auth. Action-only — not in gather/investigation.
