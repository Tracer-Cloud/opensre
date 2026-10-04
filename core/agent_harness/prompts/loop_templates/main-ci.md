---
name: Main CI
description: Fixes failing CI on the default branch.
cron: "58 * * * *"
mode: agent
---

Fix failing CI on the default branch.

1. If an open pull request already repairs the default branch, call fix_github_pr_ci with that pull request's pr_number.
2. Otherwise call fix_github_pr_ci with branch set to the default branch; it reports nothing to fix when the latest checks passed.
3. If it pushed a new repair branch, open a pull request from that branch to the default branch.
4. Never merge, and reply with the pull request link and the root cause.
