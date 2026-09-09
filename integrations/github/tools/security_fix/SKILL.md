---
name: operating-github-security-fixer
description: Use when the user asks OpenSRE to fix GitHub security and quality issues, Security and quality page findings, Code Quality standard findings, Dependabot alerts, code-scanning or CodeQL alerts, vulnerable dependencies, repo security issues, or to fix findings and optionally open a pull request.
tools:
  - fix_github_security_alert
---

# operating-github-security-fixer

Use `fix_github_security_alert` for GitHub security remediation requests, not
`github_cli` or `shell_run`.

Rules:

- Dependabot, code-scanning/CodeQL alerts, and Code Quality standard findings
  are supported.
- Secret-scanning alerts are refused by the tool; tell the user to rotate or
  revoke the secret first.
- For a broad repo request, omit `alert_number` and use `alert_type="auto"` so
  the tool selects one open supported security or quality finding by severity.
- If the user says "hey fix the security issues" without a repo, omit `owner`
  and `repo`; the tool uses the current checkout's GitHub origin.
- For GitHub `/security/code-scanning` pages or CodeQL/code-scanning backlog
  requests, pass `alert_type="code_scanning"`.
- For GitHub `/security/quality` pages or quality backlog requests, pass
  `alert_type="code_quality"`.
- The tool fixes findings itself: built-in fixers first, then an auto-detected
  coding agent CLI. Never add coding-agent advice, CLI names, or install
  commands beyond what the tool's `error` text already says.
- If the tool returns `response_text`, output exactly that text and stop.
- If no automatic patch is produced, keep the reply to one short line from
  `error`; do not say "next steps", add numbered options, list example
  commands, or ask a broad follow-up question.
- Set `open_pr=true` only when the user asks to open, raise, create, or ship a
  pull request.
- The tool runs one alert per call. Do not loop over multiple alerts unless the
  user explicitly asks to continue after the first result.
- Report the result from `summary`, `changed_files`, `branch_name`, and `pr_url`.
