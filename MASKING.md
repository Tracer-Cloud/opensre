# Masking Sensitive Infrastructure Identifiers

OpenSRE automatically masks sensitive infrastructure identifiers (hostnames, cluster names, account IDs, etc.) before sending data to external LLM models, then safely restores them in user-facing output.

## Quick Start

```python
from app.utils.masking import MaskingContext, mask_text, unmask_text

# Create context for an investigation
ctx = MaskingContext.create()

# Mask text before sending to LLM
masked = ctx.mask_text("Error in prod-cluster-01: connection to api.example.com failed")
# Result: "Error in <CLUSTER_0>: connection to <HOSTNAME_0> failed"

# Unmask LLM response
unmasked = ctx.unmask_text("Check logs for <CLUSTER_0>")
# Result: "Check logs for prod-cluster-01"
```

## Configuration

All masking settings are configurable via environment variables without code changes:

### Identifier Masking (Default: all enabled)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENSRE_MASK_HOSTNAMES` | `true` | Mask hostnames like `api.example.com` |
| `OPENSRE_MASK_ACCOUNT_IDS` | `true` | Mask AWS/GCP/Azure account IDs (12+ digits) |
| `OPENSRE_MASK_CLUSTER_NAMES` | `true` | Mask cluster names like `prod-eks-cluster-01` |
| `OPENSRE_MASK_SERVICE_NAMES` | `true` | Mask service names like `payment-api` |
| `OPENSRE_MASK_IP_ADDRESSES` | `true` | Mask IPv4 addresses |
| `OPENSRE_MASK_EMAILS` | `true` | Mask email addresses |
| `OPENSRE_MASK_CUSTOM_PATTERNS` | - | Comma-separated custom regex patterns |

### Performance & Safety

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENSRE_MASK_MAX_PLACEHOLDERS` | `1000` | Max identifiers per investigation before pass-through |
| `OPENSRE_MASK_VALIDATE_OUTPUT` | `true` | Validate LLM output for broken placeholders |
| `OPENSRE_MASK_PANIC_THRESHOLD` | `10` | Max validation errors before redacting output |

## Placeholder Format

Identifiers are replaced with stable placeholders:

| Type | Placeholder Pattern | Example |
|------|---------------------|---------|
| Hostname | `<HOSTNAME_N>` | `<HOSTNAME_0>`, `<HOSTNAME_1>` |
| Account ID | `<ACCOUNT_N>` | `<ACCOUNT_0>` |
| Cluster Name | `<CLUSTER_N>` | `<CLUSTER_0>` |
| Service Name | `<SERVICE_N>` | `<SERVICE_0>` |
| IP Address | `<IP_N>` | `<IP_0>` |
| Email | `<EMAIL_N>` | `<EMAIL_0>` |
| Custom | `<CUSTOM_N>` | `<CUSTOM_0>` |

**Key property**: The same identifier value always maps to the same placeholder within an investigation, enabling the LLM to reason about relationships (e.g., "the error in `<CLUSTER_0>` also affects `<SERVICE_0>`").

## Round-Trip Masking

The complete masking lifecycle in investigations:

```
┌─────────────────────────────────────────────────────────────────┐
│  Investigation Data                                                │
│  (contains: cluster-01, api.example.com, 123456789012)            │
└────────────────┬──────────────────────────────────────────────────┘
                 │ mask_text()
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  Masked Prompt for LLM                                           │
│  (contains: <CLUSTER_0>, <HOSTNAME_0>, <ACCOUNT_0>)              │
└────────────────┬──────────────────────────────────────────────────┘
                 │ Send to LLM
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  LLM Response                                                    │
│  (may contain: <CLUSTER_0>, <HOSTNAME_0>)                        │
└────────────────┬──────────────────────────────────────────────────┘
                 │ validate_placeholders()
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  Validation Check                                                │
│  - Detect broken/malformed placeholders                           │
│  - Detect hallucinated placeholders                               │
│  - Panic mode if errors exceed threshold                          │
└────────────────┬──────────────────────────────────────────────────┘
                 │ unmask_text()
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│  User-Facing Output                                                │
│  (contains: cluster-01, api.example.com - originals restored)     │
└──────────────────────────────────────────────────────────────────┘
```

## Panic Mode

When validation detects excessive errors (default: >10), the system enters **panic mode**:

1. Output is redacted with a safety message
2. Raw LLM response is stored in state (`_masked_raw_response`) for debugging
3. Investigation continues without exposing potentially corrupted placeholders

Example redacted output:
```
[REDACTED: Output contained invalid placeholders that could not be 
safely unmasked. Raw response stored in state._masked_raw_response for debugging.]
```

**To disable panic mode**: Set `OPENSRE_MASK_PANIC_THRESHOLD=999999`

## Disabling Masking

### Disable Specific Identifiers
```bash
export OPENSRE_MASK_CLUSTER_NAMES=false
export OPENSRE_MASK_HOSTNAMES=false
```

### Disable Validation
```bash
export OPENSRE_MASK_VALIDATE_OUTPUT=false
```

### Completely Disable Masking
```bash
# Disable all identifier types
export OPENSRE_MASK_HOSTNAMES=false
export OPENSRE_MASK_ACCOUNT_IDS=false
export OPENSRE_MASK_CLUSTER_NAMES=false
export OPENSRE_MASK_SERVICE_NAMES=false
export OPENSRE_MASK_IP_ADDRESSES=false
export OPENSRE_MASK_EMAILS=false

# Optionally disable validation overhead
export OPENSRE_MASK_VALIDATE_OUTPUT=false
```

## Custom Patterns

Add custom regex patterns via environment:

```bash
export OPENSRE_MASK_CUSTOM_PATTERNS="\bAPI_KEY_[A-Z0-9]{32}\b,\bSECRET_[a-z]+\b"
```

Patterns are Python regex syntax, comma-separated.

## Performance

- **Regex compilation**: Cached (max 32 unique policy configurations)
- **Placeholder lookup**: O(1) dict operations
- **Validation**: ~1-2ms per LLM response
- **Large text**: Tested up to 1000 lines (~20ms)
- **Memory**: Max 1000 placeholders per investigation (configurable)

## Troubleshooting

### Placeholders appearing in user output
Check logs for warnings like:
```
Warning: 3 placeholders could not be unmasked: ['<CLUSTER_999>', '<HOSTNAME_5>', '<ACCOUNT_12>']
```

This indicates the LLM hallucinated placeholders not in the mapping.

### Panic mode activating frequently
1. Check validation threshold: `OPENSRE_MASK_PANIC_THRESHOLD`
2. Inspect `_masked_raw_response` in state for debugging
3. Consider disabling validation if model is well-behaved: `OPENSRE_MASK_VALIDATE_OUTPUT=false`

### Memory warnings
```
[masking] Placeholder map at 800/1000 (80%) - approaching limit
```
Increase limit: `OPENSRE_MASK_MAX_PLACEHOLDERS=5000`

## API Reference

### Core Functions

```python
# Create context
ctx = MaskingContext.create(policy=None)  # policy loads from env if not provided

# Mask and unmask
text = ctx.mask_text("Error in prod-cluster-01")  # -> "Error in <CLUSTER_0>"
text = ctx.unmask_text("Check <CLUSTER_0>")      # -> "Check prod-cluster-01"

# Dict/list masking
masked = mask_dict(evidence, ctx)
restored = unmask_dict(masked, ctx)

# Validation
issues = validate_placeholders(llm_response, ctx.placeholder_map)
if should_panic(issues, threshold=10):
    ...

# Detect unmapped placeholders after unmasking
remaining = detect_remaining_placeholders(text)
```

### Policy Configuration

```python
from app.utils.masking import MaskingPolicy

# From environment
policy = MaskingPolicy.from_env()

# Explicit
policy = MaskingPolicy(
    mask_hostnames=True,
    mask_cluster_names=True,
    mask_account_ids=True,
    mask_service_names=True,
    mask_ip_addresses=True,
    mask_emails=True,
    custom_patterns=[r"\bAPI_KEY_\w+\b"],
    max_placeholders=1000,
    validate_output=True,
    panic_threshold=10,
)
```

## Security Considerations

1. **Per-investigation isolation**: Each investigation gets its own context
2. **No cross-contamination**: Placeholder mappings don't leak between investigations
3. **Safe defaults**: All masking enabled by default
4. **Validation**: Detects broken/malformed placeholders before user display
5. **Panic mode**: Redacts output when corruption detected
6. **Logging**: Warnings show placeholder names, never original values

## See Also

- `app/utils/masking/` - Source code
- `tests/utils/masking/` - Test suite (119 tests)
- Issue #478 - Initial masking implementation
- Issue #479 - Restoration of readable output
