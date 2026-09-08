---
name: github-security-fix
description: >-
  Remediate GitHub security / Dependabot / CodeQL / code-quality alerts via
  fix_github_security_alert
metadata:
  owner: Tracer Team
  usecases:
    - Remediate Dependabot, CodeQL, and code-quality alerts and raise a pull request
    - Fix the findings behind a security alert URL or a security and quality page URL
  requires:
    - GitHub account with write access to the target repository
    - GitHub token usable by OpenSRE with security-events read access
    - Installed and authenticated coding agent
  type: repair
  version: "1.0"
---

# GitHub security and quality fix

Use `fix_github_security_alert` to remediate a supported security or quality
finding, producing a local diff or a pull request as requested.

## When to use

- The user asks to fix/remediate GitHub security and quality issues, security
  alerts, Security and quality findings, Code Quality findings, Dependabot
  alerts, CodeQL/code-scanning alerts, vulnerable dependencies, or repo
  security issues.
- The user says "hey fix the security issues", "fix the security issues in
  owner/repo", "fix the security and quality issues", or "fix the opensre repo
  security issues and raise a PR".
- A GitHub security alert URL, `/security/code-scanning` page URL, or
  `/security/quality` URL is provided.

## Scope

Use other workflows for these requests:

- Ordinary GitHub issue/PR create, close, comment, assign, label, merge, or repo
  reads. Use `github_cli`.
- Secret-scanning remediation. The tool will refuse it because the secret must
  be revoked/rotated outside the repo before code cleanup.

## Workflow rules

- For broad repo requests, call:
  `fix_github_security_alert(owner?, repo?, alert_type="auto", open_pr=<user asked PR>)`
  and let the tool select one open supported security or quality finding.
- If no owner/repo is named, omit both and let the tool use the current
  checkout's GitHub origin.
- If the user supplies an alert URL, `/security/code-scanning` page URL, or
  `/security/quality` URL, pass it as
  `alert_url`.
- If they name a Dependabot alert, code-scanning alert, CodeQL alert, or Code
  Quality finding number, pass `alert_type` and `alert_number`.
- Use `alert_type="code_quality"` for GitHub `/security/quality` standard
  findings such as unused import, empty except, unreachable code, or mixed
  returns.
- Use `alert_type="code_scanning"` for GitHub `/security/code-scanning` pages
  or broad CodeQL/code-scanning requests.
- Set `open_pr=true` only when they ask to open/raise/create a PR or "ship" the
  fix; otherwise leave it false for a local diff.
- Never use `github_cli` to run a raw `gh` workflow around this. The fixer owns
  alert context, fix execution, branch safety, and PR creation.
- The tool fixes findings itself: built-in fixers first, then an auto-detected
  coding agent CLI (no configuration needed). Never add coding-agent advice,
  CLI names, or install commands beyond what the tool's `error` text already
  says, and never make an external agent the user's next step.
- If the tool returns `response_text`, output exactly that text and stop.
- If the tool reports no automatic patch, reply in one short line from `error`.
  Do not say "next steps", do not add numbered options, do not list example
  commands, and do not ask a broad follow-up question.
- After the tool returns, reply briefly from the result: finding type/number,
  changed files, and PR URL if present. If `error_kind` is set, explain the
  required next step from `error`.

## Examples

- "fix the security issues in Tracer-Cloud/opensre and raise a PR"

  ```text
  fix_github_security_alert(owner="Tracer-Cloud", repo="opensre", alert_type="auto", open_pr=true)
  ```

- "hey fix the security issues"

  ```text
  fix_github_security_alert(alert_type="auto")
  ```

- "fix the code quality findings on https://github.com/Tracer-Cloud/opensre/security/quality"

  ```text
  fix_github_security_alert(alert_url="https://github.com/Tracer-Cloud/opensre/security/quality", alert_type="code_quality")
  ```

- "fix Dependabot alert 12 in this repo"

  ```text
  fix_github_security_alert(alert_type="dependabot", alert_number=12)
  ```

- "fix https://github.com/acme/app/security/code-scanning/7 and open a PR"

  ```text
  fix_github_security_alert(alert_url="https://github.com/acme/app/security/code-scanning/7", open_pr=true)
  ```

- "fix the code scanning errors on https://github.com/Tracer-Cloud/opensre/security/code-scanning and raise a PR"

  ```text
  fix_github_security_alert(alert_url="https://github.com/Tracer-Cloud/opensre/security/code-scanning", alert_type="code_scanning", open_pr=true)
  ```
