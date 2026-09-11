---
name: scheduling-github-ci-fixes
description: >-
  Sets up a local scheduled loop for one repository that checks its open pull
  requests for failing GitHub Actions checks and repairs them with
  fix_github_pr_ci. Optionally creates a private demo repository with a
  deliberately broken pull request first, fixes it once end to end, and
  deletes it afterwards. Use for the startup demo option "Set up an agent
  that improves CI/CD reliability over time" or "keep our pull requests green
  automatically". A one-off fix of a named pull request is fixing-github-ci;
  a historical metrics table is analyzing-github-ci-performance.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Vincent
  last_changed_at: 2026-09-11
  usecases:
    - First-experience demo: get to a real CI fix as quickly as possible
    - Weekday loop that repairs failing pull-request checks in one repository
    - One end-to-end fix on a throwaway private repository, deleted afterwards
  requires:
    - GitHub token usable by OpenSRE with write access to the watched repository
    - Local git checkout of the watched repository (fix_github_pr_ci edits and pushes from it)
    - Installed and authenticated coding agent for fix_github_pr_ci
    - For the demo, permission to create and delete a private repository in the user's account
  type: repair
  version: "2.0"
references:
  - common/ask_once.md
---

# Scheduled CI fix loop

Set up a loop on this machine that looks at one repository's open pull
requests every weekday morning and repairs the ones whose GitHub Actions
checks are failing. Before scheduling, offer to prove the fix on a throwaway
private repository with a deliberately broken pull request, and delete that
repository afterwards.

Rules that hold for every step:

- If a tool reports a missing GitHub token, tell the user to run
  `opensre integrations setup github` and stop.
- `fix_github_pr_ci` owns the repair: never run `git commit`, `git push`,
  `gh pr checkout`, or `gh run view` around it. It pushes to the pull
  request's own branch and never to `main`.
- Every question is one `ask_user_choice` call with the exact title given
  below. End the turn after calling it; the answer arrives as the next user
  message.

## Plan

After reading this skill, use `update_plan` to create or revise the live
CI Fix Loop Progress plan using the eight numbered workflow headings below as
its steps. Keep the loop card and the cleanup as separate plan items. Mark a
step `completed` without running it when its body says it may be skipped;
never delete it. Update statuses when each step's completion condition is
met.

- [ ] Step 1. Scan local repositories with scan_local_git_workspace.
- [ ] Step 2. Select the repository to watch using ask_user_choice.
- [ ] Step 3. Ask whether to create a broken demo pull request using ask_user_choice.
- [ ] Step 4. Create the private demo repository and its failing pull request with shell_run and github_cli.
- [ ] Step 5. Repair the demo pull request with fix_github_pr_ci.
- [ ] Step 6. Schedule the fix loop with slash_invoke and show its card.
- [ ] Step 7. Ask whether to delete the demo repository using ask_user_choice.
- [ ] Step 8. Delete the demo repository and its clone with github_cli and shell_run.

## Workflow

### 1. Scan this machine

Call `scan_local_git_workspace()` with no arguments.

Skip this step and step 2 when the repository is already chosen in this
session, for example after the analytics demo handed off here: mark both
plan items `completed` and resume at step 3 with that repository. When it
was chosen from a scan, keep its `path` from that scan result.

Complete when `scan_local_git_workspace` has returned in this turn, even
with an empty result.

### 2. Pick the repository

Call `ask_user_choice` with the title `Which repository should the agent watch?`.
Offer up to 5 scanned repositories that have `has_workflows` true and a
`github` name, as `<owner/repo>`, the user's own commits first. Offer the
picker even when only one repository qualifies; a single scan result is
not a selection. Do not offer `Tracer-Cloud/opensre`: the loop pushes fixes,
so the user needs write access.

When no scanned repository qualifies, skip the picker: say so in one line,
mark this plan item `completed`, and continue to step 3, where the demo
repository becomes the watched repository if the user accepts it.

Complete when the user's answer to `ask_user_choice` has arrived as a
message, or the step was skipped. Remember the chosen repository as
`<owner>/<repo>` and its local checkout `path` as `<checkout>`.

### 3. Offer the demo

Call `ask_user_choice` with the title
`Should I create a broken pull request to demonstrate the fix?` and these
options:

- Yes, create a private demo repository with a failing check
- No, watch the repository as it is

Complete when the answer has arrived as a message. On `No`, mark steps 4, 5,
7, and 8 `completed` as skipped and resume at step 6. On `No` with no
repository chosen in step 2, say in one line that there is nothing to
watch and conclude.

### 4. Create the demo repository and its failing pull request

Call `github_cli(args=["api", "user", "--jq", ".login"])` and keep the
result as `<login>`. The demo repository is `<login>/opensre-ci-fix-demo`
and its clone is `~/opensre-ci-fix-demo`; if a repository of that name
already exists, append `-2`, `-3`, … until `repo create` succeeds.

Then run these actions in order, each as one tool call:

1. `shell_run` to build a passing project on `main`:

   ```bash
   set -e
   DEMO_DIR="$HOME/opensre-ci-fix-demo"
   rm -rf "$DEMO_DIR" && mkdir -p "$DEMO_DIR/.github/workflows" && cd "$DEMO_DIR"
   git init -q -b main
   cat > .github/workflows/ci.yml <<'EOF'
   name: CI
   on:
     pull_request:
     push:
       branches: [main]
   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: "3.12"
         - run: python -m unittest -v
   EOF
   cat > pricing.py <<'EOF'
   def total(prices: list[float], discount: float = 0.0) -> float:
       """Sum the prices and apply a fractional discount."""
       return sum(prices) * (1 - discount)
   EOF
   cat > test_pricing.py <<'EOF'
   import unittest

   from pricing import total


   class TotalTest(unittest.TestCase):
       def test_total_applies_discount(self) -> None:
           self.assertEqual(total([10.0, 20.0], discount=0.5), 15.0)

       def test_total_without_discount(self) -> None:
           self.assertEqual(total([10.0, 20.0]), 30.0)
   EOF
   git add -A && git commit -qm "Pricing helper with tests and CI"
   ```

2. `github_cli(args=["repo", "create", "<login>/opensre-ci-fix-demo", "--private", "--source", "<clone>", "--remote", "origin", "--push"])`
   with `<clone>` the absolute path of the directory above.

3. `shell_run` to break the arithmetic on a branch and push it:

   ```bash
   set -e
   cd "$HOME/opensre-ci-fix-demo" && git checkout -qb break-pricing
   cat > pricing.py <<'EOF'
   def total(prices: list[float], discount: float = 0.0) -> float:
       """Sum the prices and apply a fractional discount."""
       return sum(prices) - discount
   EOF
   git commit -qam "Simplify the discount arithmetic" && git push -qu origin break-pricing
   ```

4. `github_cli(args=["pr", "create", "--repo", "<login>/opensre-ci-fix-demo", "--head", "break-pricing", "--base", "main", "--title", "Simplify the discount arithmetic", "--body", "Demo pull request created by OpenSRE. Its CI check fails on purpose so the fix loop has something to repair."])`
   and keep the returned pull request URL as `<demo pr>`.

If any of these fails, relay the error in one line and stop; do not
improvise a different repository layout.

Complete when `pr create` has returned `<demo pr>`. If no repository was
chosen in step 2, the demo repository is now the watched repository:
`<owner>/<repo>` is `<login>/opensre-ci-fix-demo` and `<checkout>` is its
clone.

### 5. Repair the demo pull request

The check has to finish before it can be fixed. Call
`github_cli(args=["pr", "checks", "<demo pr>", "--watch"])` and wait for it
to return; it exits non-zero with `CI` listed as failed, which is the
expected result, not an error to report.

Then call `fix_github_pr_ci(pr_url="<demo pr>", workspace="<clone>")`. It
inspects the failing check, runs the coding agent, commits, pushes to
`break-pricing`, and waits for the new check. If it returns
`response_text`, put that text in your reply exactly. If `error_kind` is
set, reply with one short line from `error`, then continue to step 6; the
loop is still worth scheduling.

Complete when `fix_github_pr_ci` has returned in this turn and your reply
carries its outcome. Every figure in the reply comes from the tool result.

### 6. Schedule the fix loop

Call `slash_invoke` with `command="/loops"` and these `args`, in this
order, each as its own list element:

```text
add
--name
CI fix agent · <owner>/<repo>
--time
08:00
--weekdays
--tz
<IANA timezone of this machine, from the runtime facts; omit --tz and this value when unknown>
--channel
shell
--prompt
<tick prompt>
```

The tick prompt is one list element, with the placeholders filled in and
no line breaks:

```text
Scheduled CI fix pass for <owner>/<repo>. First call summarize_github_pr_status(owner="<owner>", repo="<repo>", state="open") and keep the pull requests whose check_status is failed. For each of them, one at a time, call fix_github_pr_ci(owner="<owner>", repo="<repo>", pr_number=<number>, workspace="<checkout>") and take its response_text as the outcome; when the tool refuses one, keep its reason and move on. Reply with one line per pull request that had a failing check: number, title, the checks that failed, and the repair commit or the reason it was left alone. Write "No failing checks on <owner>/<repo>." when none were red. Every figure comes from a tool result.
```

The tool observation carries the created loop's `id`, cron, timezone, and
next run. Reply with this card, filled from that observation:

```text
**Scheduled: CI fix agent · <owner>/<repo>**

- Runs weekdays at 08:00 <timezone>, next <next run>
- Repairs pull requests whose checks are failing and reports to this shell's inbox: `/loops messages`
- Manage: `/loops list`, `/loops stop <id>`, `/loops delete <id>`
- Runs while the shell is open; `/loops service install` keeps it running when it is not
```

Do not ask about the cadence: the card says how to change it. If the
observation reports an error, relay it in one line and stop.

Complete when `slash_invoke` has returned in this turn and your reply
contains the card.

### 7. Ask about cleanup

Skip when the user declined the demo in step 3. Otherwise call
`ask_user_choice` with the title `Delete the demo repository now?` and these
options:

- Yes, delete the demo repository and its local clone
- Keep it for now

Complete when the answer has arrived as a message. On `Keep it for now`, say
in one line that `<login>/opensre-ci-fix-demo` and `~/opensre-ci-fix-demo`
remain, mark step 8 `completed` as skipped, and conclude.

### 8. Delete the demo repository

Call `github_cli(args=["repo", "delete", "<login>/opensre-ci-fix-demo", "--yes"])`.
If it fails because the token lacks the `delete_repo` scope, tell the user to
run `gh auth refresh -h github.com -s delete_repo` and then
`gh repo delete <login>/opensre-ci-fix-demo --yes`, and continue.

Then call `shell_run` with `rm -rf "$HOME/opensre-ci-fix-demo"`.

When the demo repository is also the watched repository (no repository was
chosen in step 2), the loop from step 6 has nothing left to watch: call
`slash_invoke(command="/loops", args=["delete", "<id>"])` as well and say
that the loop was removed.

Complete when the delete calls have returned in this turn. Reply in one
line with what was removed, and conclude.
