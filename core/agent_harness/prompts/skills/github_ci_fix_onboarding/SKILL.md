---
name: github-ci-fix-onboarding
description: >-
  Onboards and troubleshoots GitHub PR CI fixing by configuring GitHub CLI
  authentication, a non-exposed GitHub token, a matching local checkout, and a
  ready coding agent before running fix_github_pr_ci. Use for first-time setup,
  failed prerequisites, demos, or action-shaped requests to onboard the user
  onto the local CI/CD fixing flow. Multi-step; load before acting.
metadata:
  owner: Tracer Team
  usecases:
    - Onboarding new users to the github-ci-fix skill
    - First-time setup of local CLI and GitHub authentication
    - Guiding a user from zero to a green CI run on a real repository
    - Troubleshooting missing prerequisites or failed first-time demos
  requires:
    - GitHub account with write access to the target repository
    - Local checkout whose origin matches the target pull request
    - GitHub token usable by OpenSRE
    - Installed and authenticated coding agent
  type: onboarding
  version: "1.4"
  prerequisite_for: core/agent_harness/prompts/skills/github_ci_fix/SKILL.md
---

# GitHub CI/CD Onboarding & Installation

## Goal

Get the user from zero to a working local CI/CD loop with zero friction.
Treat every missing prerequisite as a task the skill must fix.
Continue until one real, same-repository pull request has completed an
end-to-end CI fix cycle and its required checks are green.

## When to use

- The user asks to set up, install, onboard, troubleshoot, or demo GitHub CI
  fixing in OpenSRE.
- Action-shaped wording such as "Can you onboard me on the CI/CD flow?" means
  run this setup; it is not a request to explain the CI/CD documentation.
- `fix_github_pr_ci` is unavailable or reports a missing CLI, token, checkout,
  permission, or coding-agent prerequisite.
- The user asks to "fix CI on this PR" but first-time readiness is unknown.

Do not use this onboarding flow when prerequisites are already known to pass and
the user only wants a CI failure fixed. Load `github-ci-fix` and call
`fix_github_pr_ci` directly.
Do not use it for an explicit explanation such as "How does the CI/CD flow
work?" when the user did not ask to set up, install, onboard, demo, or fix
anything.

## Security rules

- Never run `gh auth status -t`, `gh auth token`, `env`, or any command that
  prints a token.
- Collect and persist the token only through the interactive GitHub integration
  setup prompt.
- Do not claim that `gh auth login` alone configures OpenSRE. OpenSRE must also
  be able to resolve a token from its GitHub integration or supported
  environment.
- Never manufacture a failing workflow for a demo, weaken CI, push to a
  protected branch, or use a fork PR. Use an existing same-repository PR with a
  genuine failing check.

## Ownership rules

This flow is action-owned from the first prerequisite check to the completion
report. Once loaded, drive every step with action tools and conclude with your
own reply:

- Keep this flow in the current agent turn — report progress, explain blockers,
  and summarize the final checks from the tool results. A generic answer does not know
  this workflow and will answer with unrelated GitHub status reads.
- Never call engineering-status gather tools (`generate_work_status_report`,
  `list_github_work_items`, `summarize_github_pr_status`) here. Onboarding is
  not a status report.
- When a prerequisite is blocked on the user (interactive login, PAT entry),
  conclude directly: state what passed, the exact command the user must run,
  and that you will rerun the failed check afterwards.
- When all prerequisites pass, do not stop to summarize readiness — continue to
  the target-PR steps and `fix_github_pr_ci` in the same flow.

## Decision points — mandatory structured choices

Whenever this flow reaches a point where the user must choose between a small,
fixed set of actions, call the `ask_user_choice` tool so the interactive shell
renders and owns the decision. Never write a numbered list with "Reply with 1,
2, or 3", and never ask the same question in prose. End the turn after calling
the tool; the selected or custom answer arrives as the next user message. If
the tool result says the interaction UI is unavailable, fall back to a short
numbered list.

### Uncommitted changes (most common blocker)

If `git status --short` shows any modified or untracked files that would
interfere with the end-to-end CI fix:

1. State the facts in one short paragraph (prerequisites ready, target PR,
   dirty tree). Do not paste raw tool output into the reply.
2. Immediately call `ask_user_choice` with these exact values (do not rephrase
   them):
   - `title`: `How should I handle the uncommitted changes?`
   - `options`:
     1. `Stash the changes (recommended – quick & safe)`
     2. `Commit the changes`
     3. `Use a separate git worktree`
3. End the turn and wait for the user's selection.
4. After the answer arrives, execute the corresponding action and continue the
   live fix cycle against the chosen PR:
   - Stash: `git stash push -u -m "pre-ci-fix-onboarding"` (include untracked
     files so the tree is fully clean; remind the user the stash name).
   - Commit: commit everything on the current branch with a clear WIP message;
     never push it as part of this flow.
   - Worktree: `git worktree add ../<repo>-ci-fix <default-branch>` and run the
     fix cycle from that worktree, leaving the user's checkout untouched.

## Step labeling rules (UX)

This flow has 8 workflow steps. Narrate them so the user can follow the run
without reading every command:

- Before every workflow step's tool calls, emit this exact header format as
  assistant text in the SAME response as the tool calls, then one short
  status sentence:

  ```
  ### [n/8] <step name>
  One-sentence status or question.
  ```

- Never start tool calls for a new step without its header.
- After a step's tool results are in, state its outcome in one line (start it
  with ✓ on success, ✗ plus what failed otherwise) before the next step's
  header.
- Use the workflow step's own number as n and a short form of its title as
  the name (e.g. `### [1/8] Prerequisite checks`), even when a step is
  trivial or already satisfied (a ✓ line with no tool calls is fine); never
  renumber mid-run.

Example of what the user should see:

```
### [1/8] Prerequisite checks
Running GitHub CLI, auth, scopes and API access…

✓ All prerequisites passed (davincios, required scopes present)

### [2/8] Token configuration
Verifying the token OpenSRE actually uses…
```

## Workflow

### 1. Run independent local checks in parallel

Run all four commands in one parallel batch:

```bash
gh --version
gh auth status
gh api user --jq .login
git rev-parse --show-toplevel && git remote get-url origin
```

`gh auth status` displays granted scopes without displaying the token. For a
classic PAT, require `repo` and `workflow`; require `read:org` when organization
membership or private organization repositories need it.

Remediate every failed check before continuing:

| Failure | Remediation |
| --- | --- |
| `gh` missing | macOS: `brew install gh`; Windows: `winget install GitHub.cli`; Linux: use the package instructions for the user's distribution |
| Not authenticated | Run `gh auth login` interactively |
| Required classic-PAT scope missing | Run `gh auth refresh -s repo,read:org,workflow` interactively |
| API access denied | Repair or repeat `gh auth login`, then rerun `gh api user --jq .login` |
| Not a git checkout | Ask for the target checkout path or clone the repository with the user's approval |
| Origin is not GitHub | Ask for the correct checkout; do not rewrite remotes automatically |

Package installation and authentication are interactive host changes. Give the
exact command, let the user complete it, and rerun the failed check. Do not skip
the check because remediation was suggested.

### 2. Configure the token OpenSRE actually uses

Never `shell_run` a nested OpenSRE process (`uv run opensre …`,
`python -m opensre …`). That second process does not see ESC and keeps
running after the user cancels.

Call `slash_invoke` with `/integrations verify github`. If GitHub is missing
or verification fails, end the turn and tell the user to run
`/integrations setup github` in this shell (it needs a full terminal). After
they finish, rerun verify.

The setup prompt is the only place the user should enter a PAT. Never
retrieve the token from `gh` or echo it for transfer.

### 3. Verify a coding agent

Run this non-secret readiness probe from the OpenSRE checkout:

```bash
uv run python -c 'from integrations.coding_agent import verify_coding_agent; ok, detail = verify_coding_agent(); print(("ready: " if ok else "not ready: ") + detail)'
```

Run that probe with `shell_run` from the OpenSRE checkout. `uv run python`
uses the project environment; do not wrap it in a nested `uv run opensre`
process.

The default `CODING_AGENT=auto` accepts the first ready backend among Pi,
Claude Code, and Codex. If none is ready, use the probe's detail to install or
authenticate one backend, then rerun the probe. Do not continue on a
`not ready` result.

### 4. Resolve one target repository and workspace

Each CI-fix run targets one repository and one local checkout. If the user
provides a PR URL, derive the repository from it. Otherwise, prefer the current
checkout's GitHub origin. Ask only for information that cannot be detected.

If repository discovery is needed, list recent non-fork, non-archived
repositories for the authenticated account or named organization:

```bash
login="$(gh api user --jq .login)"
gh repo list "$login" --limit 100 --json name,isArchived,isFork,pushedAt,visibility \
  --jq 'sort_by(.pushedAt) | reverse | .[] | select(.isFork == false and .isArchived == false) | "\(.name) (\(.visibility), pushed \(.pushedAt[:10]))"'
```

For multiple repositories, onboard the shared credentials and coding agent once,
then repeat workspace and permission validation per repository. There is no
organization-wide installation step.

### 5. Verify target access and PR suitability

- Confirm the workspace origin owner/repo exactly matches the target PR.
- Require `WRITE`, `MAINTAIN`, or `ADMIN` permission; admin is not required.
- Require an open, same-repository PR with at least one genuine failing GitHub
  Actions check.
- Refuse fork PRs. `fix_github_pr_ci` intentionally cannot push to them.
- Preserve unrelated local changes; never discard them. If they prevent branch
  checkout, present the structured uncommitted-changes choice defined in
  "Decision points — mandatory structured choices" — do not fall back to a
  free-text request.

If no suitable failing PR exists, report that onboarding is ready but the live
cycle cannot be completed yet. Do not create a failure merely to satisfy the
demo.

### 6. Run the first end-to-end fix

Load `github-ci-fix`, explain that the tool will check out the PR head branch,
edit files, commit, and push, then request approval.

- PR URL: `fix_github_pr_ci(pr_url="<url>")`
- Named PR: `fix_github_pr_ci(owner="<owner>", repo="<repo>", pr_number=<n>)`
- Current checkout and current-branch PR: `fix_github_pr_ci()`

Do not replace this call with raw `gh`, `github_cli`, or `shell_run`. If the tool
returns an error, remediate that specific prerequisite and retry the same call.

### 7. Verify GitHub checks

After the push, use `github_cli` only for read-only verification:

`github_cli(args=["pr", "checks", "<number>"], repo="<owner>/<repo>")`

If checks are pending, say so and check again when the user asks to continue.
If another check fails, rerun `fix_github_pr_ci` for the same PR. Never report
success while required checks are pending, skipped unexpectedly, or failing.

### 8. Report completion

Report the authenticated GitHub login, target repository, PR URL, selected
coding-agent backend, pushed branch, changed files, and final check state. Keep
secrets and token metadata out of the report. Offer a Slack notification only
when Slack is already configured and the user asks for one.