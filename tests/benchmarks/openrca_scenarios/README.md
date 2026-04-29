# OpenSRE / tracer-cloud/opensre benchmarks (Hub-only)

Scenario alerts and telemetry live on the Hugging Face dataset **[tracer-cloud/opensre](https://huggingface.co/datasets/tracer-cloud/opensre)** — they are **not** copied into this repository.

## Install Hub helpers

```bash
pip install 'opensre[opensre-hub]'
# or use the dev extra, which includes the same dependencies
```

## Stream alerts

[`datasets`](https://github.com/huggingface/datasets) streaming reads `query_alerts/*.json` lazily. By default **`stream_opensre_query_alerts` keeps `scoring_points`** on each alert so saved JSON works with **`opensre investigate --evaluate`**. Pass **`strip_scoring_points=True`** only if you want a rubric-free stream (e.g. exporting blind fixtures).

**Investigations** still hide the rubric from the agent when **`--evaluate` is off**: `make_initial_state` strips `scoring_points` from the in-graph `raw_alert` after load. With **`--evaluate`**, the rubric is copied to `opensre_eval_rubric`, then stripped from `raw_alert` for the agent, then the judge runs at the end.

```python
from app.integrations.opensre import stream_opensre_query_alerts

for alert in stream_opensre_query_alerts(
    query_alerts_prefix="Market/cloudbed-1/query_alerts",
):
    ...
```

Telemetry CSVs are downloaded **on first use** into `~/.cache/opensre/hf/` (override with `OPENSRE_HF_CACHE`). Set:

```bash
export OPENSRE_HF_DATASET_ID=tracer-cloud/opensre
```

OpenSRE resolves `openrca_telemetry_relative` when present; otherwise it **infers** `Market/.../telemetry/YYYY_MM_DD` from the alert text. Disable inference with `OPENSRE_INFER_TELEMETRY=0`.

## Makefile (quick path for contributors)

From the repo root (after `make install` and Hub auth if needed):

```bash
make opensre-hub-investigate
# Another scenario:
make opensre-hub-investigate OPENSRE_QUERY_PREFIX=Telecom/query_alerts
# Fetch only (writes OPENSRE_HUB_ALERT, default /tmp/opensre-hub-alert.json):
make opensre-hub-fetch OPENSRE_QUERY_PREFIX=Bank/query_alerts
# Nth alert in stream order (0-based): second alert => index 1
make opensre-hub-fetch OPENSRE_QUERY_PREFIX=Bank/query_alerts OPENSRE_HUB_INDEX=1
# Save many alerts as numbered JSON (0000.json …):
make opensre-hub-export OPENSRE_QUERY_PREFIX=Bank/query_alerts OPENSRE_EXPORT_DIR=./bank_alerts OPENSRE_EXPORT_LIMIT=25
```

Use `OPENSRE_INVESTIGATE_FLAGS=` to run without `--evaluate`. Override output with `OPENSRE_HUB_ALERT=/path/to/file.json`.

Streaming order is whatever the Hugging Face `datasets` glob returns for that prefix (stable enough for a given revision, not necessarily alphabetical by filename).

## CLI investigate

Download one alert JSON to a temp file:

```bash
export OPENSRE_HF_DATASET_ID=tracer-cloud/opensre
python - <<'PY'
import json, tempfile, pathlib
from app.integrations.opensre import stream_opensre_query_alerts
alert = next(stream_opensre_query_alerts(query_alerts_prefix="Market/cloudbed-1/query_alerts"))
path = pathlib.Path(tempfile.gettempdir()) / "opensre-hub-alert.json"
path.write_text(json.dumps(alert, indent=2))
print(path)
PY
opensre investigate -i /path/from/above/opensre-hub-alert.json
```

## LLM eval vs OpenRCA rubric (in-graph)

Use **`--evaluate`** so that after the final diagnosis the graph runs an LLM judge against `scoring_points`. The default Hub stream already includes them in the file; the agent still does not see the rubric during the run.

If you pass **`--evaluate`** but the alert has no `scoring_points`, the CLI returns **`opensre_llm_eval`** with **`skipped`: true** and a short reason.

```bash
opensre investigate -i /path/to/hub-alert.json --evaluate
```

The **`opensre_llm_eval`** object is included in the investigation JSON on **stdout** (or in **`-o`** when set). Pipe with `| jq .opensre_llm_eval` if you want only the eval block.

## Full clone (optional)

If you prefer a single `git lfs`-free folder instead of streaming + partial snapshots:

```bash
huggingface-cli download tracer-cloud/opensre --repo-type dataset --local-dir ~/data/w3joe-opensre
export OPENSRE_DATASET_ROOT="$HOME/data/w3joe-opensre"
# alerts: use JSON files under e.g. Market/cloudbed-1/query_alerts/
```
