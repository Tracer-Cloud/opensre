---
name: PR CI
description: Fixes failing CI checks on open pull requests.
cron: "28 * * * *"
mode: agent
---

Fix failing CI checks on open pull requests.

1. Call summarize_github_pr_status for this repository; it reports nothing to fix when no repairable pull request has a failed check or a merge conflict.
2. Otherwise pick one such pull request and call fix_github_pr_ci with its pr_number.
3. Never force-push or merge, and reply with the pull request link and what changed.
