---
name: Code scanning
description: Fixes open code scanning quality alerts, never security ones.
cron: "58 * * * *"
mode: agent
---

Fix open code scanning quality alerts.

1. Call fix_github_security_alert with alert_type code_scanning, quality_only true and open_pr true.
2. It picks one open alert that no open pull request already fixes, and reports nothing to fix when none is left.
3. Never merge, and reply with the pull request link and the alert it fixes.
