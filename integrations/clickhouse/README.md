# ClickHouse investigation reader: developer reference

The source checkout also provides four bounded ClickHouse evidence operations.
These are a reader API for building an investigator; the watch above continues
to use its original one-shot synthesis. No additional model tools are enabled
by this API, and it makes no model calls or application changes.

| Operation | Evidence returned |
| --- | --- |
| `compare_service_windows()` | Request/error measurements for the incident and an explicitly permitted baseline; an error-ratio delta only when both meet traffic/freshness checks. |
| `summarize_failure_patterns()` | Exact operation/message groups over stored error spans, counts of stored rows/distinct trace IDs, and a representative trace ID. |
| `get_trace(trace_id)` | Selected spans for one trace inside the permitted service/environment/window, including parent links, timing and selected dependency/version attributes. |
| `get_trace_logs(trace_id, span_id=...)` | Correlated logs, optionally filtered to one span. |

Pin the allowed windows once from the incident record. For a recent example
against the running demo:

```python
import time
from dataclasses import asdict

from config.constants.telemetry import TELEMETRY_CLICKHOUSE_PASSWORD_ENV
from config.llm_credentials import resolve_env_credential
from core.telemetry.evidence_queries import InvestigationScope
from core.telemetry.models import Scope, WatchRule
from integrations.clickhouse.investigation_queries import ClickHouseInvestigationReader
from integrations.clickhouse.telemetry import ClickHouseEvidenceConfig

rule = WatchRule(service="payment", environment="demo")
incident = rule.scope(time.time())
baseline = Scope("payment", "demo", incident.start - 30, incident.start)
reader = ClickHouseInvestigationReader(
    ClickHouseEvidenceConfig(),
    resolve_env_credential(TELEMETRY_CLICKHOUSE_PASSWORD_ENV),
    InvestigationScope(incident, baseline, minimum_requests=5),
    rule.metric,
)
try:
    print(asdict(reader.compare_service_windows()))
    patterns = reader.summarize_failure_patterns(limit=5)
    if patterns.data:
        trace_id = patterns.data[0].example_trace_id
        print(asdict(reader.get_trace(trace_id, limit=10)))
        print(asdict(reader.get_trace_logs(trace_id, limit=10)))
finally:
    reader.close()
```

Use the same credential setup as the watch. The account is checked for
database-enforced read-only access before the first evidence operation. Readers
are sequential, short-lived objects; close the reader when finished.

Required inputs are the source configuration, reader credential and incident
scope. Baseline is optional; requesting baseline evidence without it raises
`ValueError` before accessing the source. If supplied, it must be an earlier,
nonoverlapping, equal-duration window for the same service/environment. Each
window is at most five minutes; incident end to baseline start is at most one
hour. The baseline is authorized history, not an assertion that it was healthy.

Trace/log operations and pattern summaries accept `window="incident"` (default)
or `window="baseline"`. `limit` defaults to ten and accepts 1–10. Trace IDs must
be nonzero 32-character hexadecimal IDs; optional span IDs must be nonzero
16-character hexadecimal IDs. Service, environment, timestamps, credentials and
SQL are not operation arguments, so evidence reads stay inside the pinned scope.
Window endpoints must be whole Unix seconds; fractional endpoints are rejected
before any source access, so both evidence and inherited measurement queries
use the exact permitted boundaries. Windows use `(start, end]` timestamp
boundaries. Spans are selected by start time.

Each typed result identifies its source, queried windows, observation time,
execution status, truncation and limitations. Individual patterns/spans/logs
have evidence references. `empty` means the query succeeded with no matching
evidence; `unavailable` means access/query/decoding failed and includes an
exception type, never the exception message or credentials. Invalid scopes, IDs
and limits raise `ValueError` before querying.

All reads use the existing SDK byte, row, execution-time and scan limits.
Fetching one extra row detects row truncation. Oversized selected text fields
are replaced entirely with `[OMITTED:oversize]`, with truncation exposed; no
prefix is returned that could contain part of a credential. Other selected
text passes through the known credential/PII sanitizer before evidence is
returned; it remains limited redaction, not comprehensive DLP.

Pattern counts describe stored spans and trace IDs, including possible duplicate
exports; they are not authoritative failed-request counts. A trace result is
always a scoped subset, even when `truncated` is false. Other services, spans
starting outside the window, sampled/dropped telemetry and late arrivals may
be absent. Logs without trace correlation cannot be recovered by these
operations. Missing dependency/version attributes stay empty. Metric freshness
checks cannot exclude gaps within a window; missing/insufficient measurements
yield ineligible observations and no ratio delta. Invalid measurements yield
unavailable evidence. Recovery decisions remain with the watch.

Run the [live query acceptance test](../../docs/guides/otel-payment-triage.mdx#live-evidence-query-acceptance-test) against a disposable local stack before changing the SQL/schema contract.
