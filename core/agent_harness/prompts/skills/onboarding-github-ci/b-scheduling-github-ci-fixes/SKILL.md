---
name: onboarding-scheduling-github-ci-fixes
description: >-
  Sets up ongoing local monitoring of one repository's open pull requests,
  automatically editing, testing, and pushing fixes for failing GitHub Actions
  checks. Offers a disposable private-repository demonstration when no target
  PR was supplied. Use for recurring PR repair or the local CI onboarding demo.
  A one-off PR repair belongs to fixing-github-ci.
getting_started: Set up an agent that improves CI/CD reliability over time
demo_order: 2
metadata:
  owner: Vincent
  last_changed_by: Jan
  last_changed_at: 2026-09-12
  usecases:
    - Keep repairing failing pull requests across one repository over time
    - Demonstrate a scheduled repair in a private repository, then clean it up
  requires:
    - GitHub write access to the watched repository and an authenticated coding agent
    - A matching local checkout available to the existing scheduler host
    - For the demo, permission to create and delete a private repository and its workflow
  type: repair
  version: "4.0"
references:
  - common/ask_once.md
  - core/agent_harness/prompts/skills/fixing-github-ci
---

# Onboarding for Scheduled CI fixes

Monitor one repository every **2 minutes** and automatically edit, test, and
push fixes to one failing PR branch per tick. 

A green PR does not stop monitoring.

The optional private demo uses the same repair policy every **minute**; its
result is saved before its temporary resources are deleted.

## Goal
The objective of this skill is to get users to setup a scheduled loop that monitors a CICD pipeline and get a fast time to value by showcasing an example fix fast. 

## Plan

Demonstrate that a scheduled local worker can detect a failing GitHub PR,
invoke `fixing-github-ci`, and verify the resulting fix.

Track progress with `update_plan`: 

- [ ] Step 0. Run pre-requisite checks 
  - Verifying GitHub access
  - Local scheduler


- [ ] Step 1. Select the repository.
  - Use the repository already specified by the user.
  - If none is specified, ask the user to select one or choose a private
    demo repository.
  - Complete when the repository and permitted demo scope are established.

- [ ] Step 2. Check prerequisites.
  - Verify repository access, GitHub authentication, availability of
    `fixing-github-ci`, and a local scheduler capable of invoking it.
  - Read the repository's contribution and CI requirements.
  - If a prerequisite is unavailable, report the blocker before creating
    demo resources.
  - Complete when the required capabilities are verified.

- [ ] Step 3. Select the failure scenario.
  - Use a user-selected failing PR, or briefly inspect open PRs for a
    suitable CI failure.
  - If none is suitable, offer to create a private demo repository or an
    isolated demo PR containing a reproducible test failure.
  - Complete when an existing PR is selected or demo creation is authorized.

- [ ] Step 4. Start the local demo loop.
  - Scope the loop to the selected repository and PR or reserved demo branch.
  - Poll every 20 seconds for responsive demo feedback.
  - Default to a 30-minute expiry and at most three repair attempts to bound
    unattended runtime and repeated changes.
  - Allow only one active repair per PR. Record handled runs so polling
    cannot repeatedly dispatch the same failure.
  - Record the loop ID and verify that the worker starts.
  - Complete when the scoped loop is running and its expiry is configured.
  - If creating an example PR fix:
    - The PR example needs to be simple, understandable and relatable (foo-bar for instance, and not obscure cython)
    - Create a simple github actions test that executes very quickly

- [ ] Step 5. Establish and verify the failure.
  - For an existing failing PR, record its failed run, check, and commit.
  - For an authorized demo, create the isolated failure, open the PR, and
    wait for the intended test to fail.
  - Complete when GitHub reports the expected failure on the selected PR.

- [ ] Step 6. Verify detection and repair execution.
  - Confirm that worker logs identify the selected PR and failed run.
  - Confirm that the worker invoked `fixing-github-ci` and began diagnosis.
  - Let the worker perform the repair so the demo tests scheduled execution.
  - Complete when the failure is linked to an identifiable repair execution.

- [ ] Step 7. Verify the repair.
  - Record the fix commit and inspect the resulting checks.
  - Require the original failure to be resolved and the latest PR commit
    to satisfy the repository's required CI checks.
  - Verify that the fix addresses the cause and preserves the intended test.
  - If verification fails, allow another repair within the configured limits.
  - Complete when verification passes; otherwise record the blocker or limit.

- [ ] Step 8. Clean up.
  - Run cleanup after success, failure, timeout, or cancellation.
  - Stop further dispatches, stop or finish active work within the demo
    limits, and delete the demo loop. Verify it is no longer running.
  - Preserve evidence needed to explain the outcome.
  - Identify created demo resources and ask for confirmation before deleting
    branches unless that cleanup was already authorized.
  - Complete when the loop is removed and remaining resources are documented.

- [ ] Step 9. Report the outcome.
  - Report success, failure, or incomplete verification.
  - Include the PR, original failed run, worker execution, fix commit,
    final CI results, and cleanup status.
  - Claim demo success only when detection, scheduled repair, passing
    verification, and loop removal are all evidenced.

- [ ] Step 10. Offer an optional follow-up.
  - Offer remote continuous CI monitoring if relevant to the user's goal.
  - Treat remote setup as a separate task requiring its own scope and
    authorization. It is not a condition of demo completion.