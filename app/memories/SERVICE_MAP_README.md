# Service Map - Self-Learning Infrastructure Discovery

## Overview
The service map is a global asset inventory and connectivity graph that tracks discovered infrastructure across investigations. It enables the agent to build an understanding of the customer's cloud infrastructure over time, strengthening memory and enabling faster future investigations.

## Architecture

### Core Components
- **Service Map Builder** (`app/agent/memory/service_map.py`) - Extracts assets and infers edges
- **Incremental Updates** (`app/agent/nodes/investigate/node.py`) - Updates map after each investigate cycle
- **Memory Integration** (`app/agent/memory/formatter.py`) - Embeds compact map in memory files
- **Config** (`app/agent/memory/service_map_config.py`) - Toggle (default ON)

### Data Structure

```json
{
  "enabled": true,
  "last_updated": "2026-02-01T18:47:00Z",
  "assets": [
    {
      "id": "lambda:my-function",
      "type": "lambda",
      "name": "my-function",
      "investigation_count": 3,
      "last_investigated": "2026-02-01T18:47:00Z",
      "confidence": 1.0,
      "verification_status": "verified",
      "pipeline_context": ["pipeline_a", "pipeline_b"],
      "alert_context": ["Alert 1", "Alert 2"],
      "metadata": {"role": "trigger", "runtime": "python3.9"}
    }
  ],
  "edges": [
    {
      "from_asset": "lambda:my-function",
      "to_asset": "s3_bucket:my-bucket",
      "type": "writes_to",
      "confidence": 0.9,
      "verification_status": "verified",
      "first_seen": "2026-02-01T18:00:00Z",
      "last_seen": "2026-02-01T18:47:00Z"
    }
  ],
  "history": [
    {
      "timestamp": "2026-02-01T18:47:00Z",
      "change_type": "asset_added",
      "asset_id": "lambda:my-function",
      "details": "New asset: lambda my-function"
    }
  ]
}
```

## Asset Types
- `lambda` - AWS Lambda functions
- `s3_bucket` - S3 buckets (deduplicated by name, multiple keys tracked)
- `ecs_cluster` - ECS/Fargate clusters
- `batch_queue` - AWS Batch job queues
- `cloudwatch_log_group` - CloudWatch log groups
- `pipeline` - Data pipelines (Prefect, Airflow, Flink)
- `external_api` - External vendor APIs (inferred from audit payloads)
- `api_gateway` - API Gateway endpoints

## Edge Types
- `writes_to` - Asset writes data to another asset (Lambda → S3)
- `triggers` - Asset triggers another asset (External API → Lambda)
- `runs_on` - Pipeline runs on compute (Pipeline → ECS)
- `logs_to` - Asset logs to CloudWatch (Lambda → Log Group)

## Features

### 1. Incremental Discovery
Service map updates after **each investigate cycle**, not just at the end:
- Assets discovered → Immediately added to map
- Edges inferred → Immediately added to map
- Hotspots updated → investigation_count incremented

### 2. Investigation Hotspots
Assets that appear in multiple investigations are tracked:
```python
"investigation_count": 3  # Appeared in 3 investigations
"last_investigated": "2026-02-01T18:47:00Z"
```

**Value**: Hotspots indicate critical shared dependencies (e.g., External API used by multiple pipelines)

### 3. Change History (Last 20)
Every asset/edge addition is tracked:
```json
{
  "timestamp": "2026-02-01T18:47:00Z",
  "change_type": "asset_added",
  "asset_id": "lambda:my-function",
  "details": "New asset: lambda my-function"
}
```

**Value**: Audit trail of infrastructure discovery

### 4. Tentative Assets & Edges
When alert context suggests relationships but evidence is partial:
```python
{
  "confidence": 0.6,
  "verification_status": "needs_verification",
  "metadata": {"inferred_from": "alert_text"}
}
```

**Value**: Gives the agent something to investigate even with incomplete data

### 5. Memory Embedding
Service map is embedded in memory files:

```markdown
## Asset Inventory
- external_api: https://api.vendor.com (investigated 3x, confidence=0.8)
- s3_bucket: my-landing-bucket (investigated 2x, confidence=1.0)
...

## Service Map
{
  "assets": [...],
  "edges": [...],
  "total_assets": 11,
  "total_edges": 3
}
```

**Value**: Future investigations can leverage historical infrastructure knowledge

## Usage

### Toggle On/Off
Edit `app/agent/memory/service_map_config.py`:
```python
SERVICE_MAP_ENABLED = True  # or False for empty state
```

When disabled, service map returns empty state:
```json
{
  "enabled": false,
  "assets": [],
  "edges": [],
  "history": []
}
```

### Access Service Map
```python
from app.agent.memory.service_map import load_service_map

service_map = load_service_map()
assets = service_map["assets"]
edges = service_map["edges"]
```

### Build Service Map
```python
from app.agent.memory.service_map import build_service_map, persist_service_map

service_map = build_service_map(
    evidence=evidence,
    raw_alert=raw_alert,
    context=context,
    pipeline_name="my_pipeline",
    alert_name="My Alert"
)

persist_service_map(service_map)
```

## Real-World Impact

After 3 investigations across different pipelines:
- **11 assets** discovered (4 S3 buckets, 3 ECS clusters, 3 pipelines, 1 external API)
- **3 edges** created (Pipeline → ECS relationships)
- **External API identified as shared dependency** (3x hotspot)

### Benefits
1. **Skip correlation steps**: Agent already knows External API → Lambda → S3 → Pipeline flow
2. **Prioritize investigation paths**: Check hotspots first (external API likely culprit)
3. **Enable parallel investigation**: If External API down, investigate all 3 pipelines simultaneously
4. **Detect anomalies**: New asset types, unexpected edges, missing expected assets

### Performance Impact (VALIDATED)

**Current Status** (as of 2026-02-01):
```
WITHOUT service map: 30.09s (baseline)
WITH service map:    35.05s (current)
Impact:             -16.5% (slower)
```

**Why**: Service map tracks assets but doesn't yet optimize actions (pure overhead).

**Future** (after action-skipping implemented):
```
Target:             ~23-25s
Improvement:        +25-30% faster
Mechanism:          Skip known asset discovery, prioritize hotspots
```

**Configuration**: Default OFF (`SERVICE_MAP_ENABLED = False`) until optimization complete

## File Locations
- **Service Map JSON**: `app/memories/service_map.json`
- **Memory Files**: `app/memories/YYYY-MM-DD-<pipeline>-<alert_id>.md`
- **Experiment Results**: `app/memories/SERVICE_MAP_EXPERIMENTS.md`

## Testing
```bash
# Run service map tests
python3 -m pytest app/agent/memory/service_map_test.py -v

# Run all memory tests (includes service map)
python3 -m pytest app/agent/memory/ -v
```

## Performance
- Service map update: ~50-100ms per investigate cycle
- Memory file size increase: ~2-3KB for service map section
- JSON file size: ~5-10KB for 10 assets + 5 edges + 20 history entries
- Parser extraction: <10ms

## Future Enhancements
1. Lambda → S3 edge enhancement (improve source detection)
2. CloudWatch log group associations
3. Tentative edge verification tracking
4. Cross-pipeline query helpers
5. Mermaid diagram generation from service map
