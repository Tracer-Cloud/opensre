---
name: Code scanning
description: Fixes open code scanning alerts on the default branch.
cron: "58 * * * *"
mode: agent
---

Fix open code scanning alerts.

1. List open code scanning alerts on the default branch.
2. Skip alerts that an open pull request already fixes.
3. Pick one alert, or a few with the same rule in the same file, and read the flagged code.
4. Fix the underlying problem without suppressing the alert or weakening a test.
5. Run the relevant tests locally and open a pull request that names the alerts it fixes.
6. Never merge, and reply with the pull request link.
