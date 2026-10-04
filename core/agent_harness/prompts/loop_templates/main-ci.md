---
name: Main CI
description: Fixes failing CI on the default branch.
cron: "58 * * * *"
mode: agent
---

Fix failing CI on the default branch.

1. Skip this run if an open pull request already fixes the default branch's failing check.
2. Otherwise call fix_github_pr_ci with branch set to the default branch; it reports nothing to fix when the latest checks passed.
3. If it pushed a repair branch, open a pull request from that branch to the default branch.
4. Never merge, and reply with the pull request link and the root cause.
