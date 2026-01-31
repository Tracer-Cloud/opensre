# Why Sources Exist: Architecture Decision

## The Question
Why have an intermediate "sources" layer between alert annotations and investigation actions? Why not just pass all actions directly to the LLM?

## The Answer: Action Filtering & Context Hints

### Without Sources (Direct to Actions)
```python
# BAD: Pass all 14 actions to LLM, regardless of whether they can run
all_actions = [
    "get_cloudwatch_logs",      # Needs: log_group
    "inspect_s3_object",         # Needs: bucket, key
    "get_lambda_invocation_logs",# Needs: function_name
    "inspect_lambda_function",   # Needs: function_name
    "get_s3_object",            # Needs: bucket, key
    ...
]
# LLM might plan: ["inspect_lambda_function"]
# Result: ❌ FAILS - no function_name in annotations
```

### With Sources (Current Architecture)
```python
# GOOD: Filter actions based on available data
sources = detect_sources(alert)  # Extract: log_group, bucket, function_name
available_actions = [
    action for action in all_actions
    if action.availability_check(sources)  # Only include if data exists
]
# LLM only sees actions that CAN actually run
```

## Empirical Evidence

### Test Case: Minimal vs Full Annotations

**Minimal Annotations** (just log_group and s3_key):
```python
annotations = {
    "cloudwatch_log_group": "/ecs/tracer-flink",
    "s3_key": "data.json",  # ← Missing bucket!
}
```
**Result**: Only **2/14 actions** available (14%)
- ❌ Can't inspect S3 (no bucket)
- ❌ Can't check Lambda (no function_name)
- ❌ Can't get audit trail (no audit_key)

**Full Annotations** (complete context):
```python
annotations = {
    "cloudwatch_log_group": "/ecs/tracer-flink",
    "s3_bucket": "my-bucket",
    "s3_key": "data.json",
    "audit_key": "audit/data.json",
    "processed_bucket": "processed-bucket",
    "function_name": "my-lambda",
    "ecs_cluster": "my-cluster",
}
```
**Result**: **10/14 actions** available (71%)
- ✅ Can inspect S3 (has bucket + key)
- ✅ Can check Lambda (has function_name)
- ✅ Can get audit trail (has audit_key)
- ✅ Can verify processed output (has processed_bucket)

## Why This Matters

### 1. **Prevents LLM from Planning Doomed Actions**
Without sources, the LLM might plan `inspect_lambda_function` when no `function_name` exists. The action would fail, wasting a loop iteration.

### 2. **Reduces Token Usage**
Why send the LLM 14 actions when only 2 can run? Sources filter the action list to only relevant options.

### 3. **Provides Structured Context**
Sources generate helpful hints for the LLM:
```
CloudWatch Logs Available:
- Log Group: /ecs/tracer-flink
- Use get_cloudwatch_logs to fetch error logs

S3 Audit Trail Available:
- Bucket: my-bucket
- Key: audit/flink-20260131.json
- Use get_s3_object to fetch external API request/response
```

This is much better than dumping raw annotations.

### 4. **Auto-Extracts Parameters**
Each action has a `parameter_extractor` that pulls values from sources:

```python
parameter_extractor=lambda sources: {
    "bucket": sources.get("s3", {}).get("bucket"),
    "key": sources.get("s3", {}).get("key"),
}
```

Without sources, the investigate node would need to manually extract these from annotations.

## Alternative: Direct to Actions

If we removed sources, we'd need:

1. **Every action checks annotations directly**
   ```python
   def inspect_s3_object(annotations, ...):
       bucket = annotations.get("s3_bucket") or annotations.get("bucket") or ...
       if not bucket:
           raise ValueError("No bucket in annotations!")
   ```

2. **LLM sees all 14 actions always**
   - Wastes tokens
   - LLM must guess which will work
   - More planning failures

3. **No structured context hints**
   - LLM just sees raw annotation dump
   - Harder to understand what data is available

## Conclusion

**Sources are an abstraction layer that:**
1. ✅ Extracts structured parameters from messy annotations
2. ✅ Filters actions to only those that can actually run
3. ✅ Provides clear hints to the LLM about available data
4. ✅ Reduces token usage by showing only relevant actions
5. ✅ Prevents wasted investigation loops from failed actions

**Trade-off:**
- Adds complexity (detect_sources.py + availability_check lambdas)
- But significantly improves investigation success rate and efficiency

## Could We Simplify?

Potentially, yes - but you'd lose benefits:
- **Option 1**: Remove sources, pass all 14 actions to LLM
  - Result: LLM plans actions that fail → wasted loops
- **Option 2**: Remove sources, make actions self-checking
  - Result: Actions need to parse annotations themselves → code duplication
- **Current**: Sources as capability discovery layer
  - Result: Clean separation, filtered actions, structured hints ✅
