# Guardrails: Sensitive Information Protection

Guardrails intercepts content before every LLM call and applies configurable
rules to detect, redact, block, or audit sensitive information.

## Quick start

```shell
# Generate a starter config with common patterns
opensre guardrails init

# Test it against sample text
opensre guardrails test "my key is AKIAIOSFODNN7EXAMPLE"

# View configured rules
opensre guardrails rules
```

## How it works

1. Rules are loaded from `~/.opensre/guardrails.yml` on first LLM call
2. Before every LLM API request, all message content is scanned against the rules
3. Depending on the rule action:
   - **redact**: matched text is replaced with `[REDACTED:<rule_name>]`
   - **block**: the request is rejected with a `GuardrailBlockedError`
   - **audit**: the match is logged but text passes through unchanged
4. All matches are written to `~/.opensre/guardrail_audit.jsonl`

If no `guardrails.yml` exists, all content passes through unchanged with zero
overhead.

## Configuration

The config file lives at `~/.opensre/guardrails.yml`. Each rule can use
regex patterns, keyword lists, or both.

```yaml
rules:
  - name: aws_access_key
    description: "AWS access key IDs"
    action: redact
    patterns:
      - "(?:AKIA|ASIA)[A-Z0-9]{16}"

  - name: credit_card
    description: "Credit card numbers"
    action: block
    patterns:
      - "\\b\\d{4}[- ]?\\d{4}[- ]?\\d{4}[- ]?\\d{4}\\b"

  - name: internal_domains
    description: "Internal hostnames that should not leak"
    action: audit
    keywords:
      - "prod-db.internal.corp"
      - "staging.internal.corp"

  - name: pii_fields
    description: "Common PII field names"
    action: redact
    keywords:
      - "social_security"
      - "date_of_birth"
    replacement: "[PII_REDACTED]"
```

### Rule fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique identifier for the rule |
| `action` | no | `redact`, `block`, or `audit` (default: `audit`) |
| `patterns` | no* | List of regex patterns (case-insensitive) |
| `keywords` | no* | List of literal keywords (case-insensitive) |
| `description` | no | Human-readable description |
| `replacement` | no | Custom replacement text (default: `[REDACTED:<name>]`) |
| `enabled` | no | Set to `false` to disable without removing (default: `true`) |

*At least one of `patterns` or `keywords` is required.

## CLI commands

### `opensre guardrails init`

Creates a starter `~/.opensre/guardrails.yml` with common patterns for AWS
keys, credit cards, private keys, and API tokens. Does not overwrite an
existing config.

### `opensre guardrails test "text"`

Dry-run: scans the provided text against all rules and shows what would be
matched, redacted, or blocked.

```
$ opensre guardrails test "key=AKIAIOSFODNN7EXAMPLE"
  [REDACT] aws_access_key: matched 'AKIAIOSFODNN7EXAMPLE'

  Redacted output: key=[REDACTED:aws_access_key]
```

### `opensre guardrails rules`

Lists all configured rules with their action and status.

### `opensre guardrails audit`

Shows recent entries from the audit log at `~/.opensre/guardrail_audit.jsonl`.

## Health check

`opensre health` shows the current guardrails status:

```
CLI
  environment: development
  integration store: ~/.tracer/integrations.json
  guardrails: 5 rules active (~/.opensre/guardrails.yml)
```

## Coverage

Guardrails protect all LLM call paths:

- Custom Anthropic client (`LLMClient.invoke`)
- OpenAI-compatible client (`OpenAILLMClient.invoke`)
- Structured output calls (delegated to base client)
- LangChain chat nodes (`chat_agent_node`, `general_node`)
- Alert extraction prompts
- Root cause diagnosis prompts
- Action planning prompts

## Common patterns

Here are useful patterns you can add to your config:

```yaml
# Email addresses
- name: email
  action: redact
  patterns:
    - "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}"

# IPv4 addresses
- name: ipv4
  action: audit
  patterns:
    - "\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b"

# GitHub personal access tokens
- name: github_pat
  action: redact
  patterns:
    - "ghp_[a-zA-Z0-9]{36}"
    - "github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}"

# Slack webhook URLs
- name: slack_webhook
  action: redact
  patterns:
    - "https://hooks\\.slack\\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[a-zA-Z0-9]+"

# JWT tokens
- name: jwt
  action: redact
  patterns:
    - "eyJ[A-Za-z0-9_-]+\\.eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+"
```
