# SigNoz automatic alert triage

`/triage` opens the interactive-shell menu. The equivalent terminal commands
start with `opensre triage`. The gateway owns investigations; closing the shell
does not stop accepted work. Run `opensre gateway start` for live intake.

## Local payment-error demonstration

Start `/triage demo` (or `opensre triage demo`). It prepares an isolated SigNoz
stack using official Foundry and a pinned OpenTelemetry demo, provisions an
admin for setup and a **viewer-only** service account for queries, configures a
native webhook and a payment-error-span alert, verifies a healthy synthetic
checkout and queryable baseline traces, enables `paymentFailure`, and waits for
a real alert and the gateway's automatic report. The investigator receives the
alert and scoped telemetry; it does not receive the injected-fault setting.

Requirements: an existing Docker daemon and Compose v2, Linux/macOS or Linux in
WSL on amd64/arm64, at least 8 GiB Docker RAM and 15 GiB free disk, Internet
access to upstream release downloads/container registries, and a configured
OpenSRE model. Model calls can incur your provider's normal charges. Setup does
not install Docker, change paging, configure a tunnel, or install a service at
boot. An existing gateway is reused; otherwise the normal gateway daemon is
started. Its normal authentication still protects generic remote `/alerts` and
prompt intake. The demo's native endpoint always requires source Basic auth.

The shell returns after three seconds while setup continues. Read the printed
demo ID and use:

```text
/triage demo status <demo-id>
/triage list
/triage show <occurrence-id>
/triage demo reset <demo-id>
/triage demo cleanup <demo-id>
```

`reset` disables the fault, verifies checkout recovery, and waits for the real
SigNoz resolved notification. Evaluation and notification-group timing can take
several minutes. A timeout records the last successful stage; inspect status and
resume setup with `/triage demo --id <demo-id>`. Completed setup is idempotent.
`cleanup` removes only the manifest's validated, namespace-owned Compose
containers/networks/volumes and demo credentials. It retains reports and leaves
the gateway running. It never prunes Docker or removes unrelated resources.

Application and SigNoz UI ports bind loopback; OTLP is internal to the dedicated
network. Host filesystem and Docker-socket telemetry mounts are removed. Raw-PII
emission stays disabled, and checkouts use synthetic fixture data. Image tags are
resolved to digests before launching, and those digests are saved in the demo
manifest. Foundry runs with telemetry and updater disabled. Its unused histogram
binary downloader is disabled because this demonstration uses `count()`.

Release inputs: Foundry **v0.3.0** (platform release checksums), SigNoz
**v0.145.0**, SigNoz collector **0.144.10** (collector versions do not track
SigNoz application versions one-to-one), and OpenTelemetry demo **3.1.0** source
commit `dedc0178918e260823323b8d95005a8cb924b007`. Downloads are checksum checked
before extraction/execution. Missing tags, pull failures, or incompatible APIs
stop setup with a resumable checkpoint; they do not silently substitute latest.
Real Docker/model E2E verification is performed separately by the operator.

## Guided connection to your SigNoz

Choose `/triage connect`. The form requires a source name, query URL, exact
allowed service names, and the gateway ingress URL **reachable from SigNoz**.
The existing SigNoz query URL/key can be reused, but the source is independent
of ordinary chat integrations. Use a query-only service-account key. An empty
scoped query proves API access, not the presence of telemetry.

For noninteractive setup, place the query key in a credential/environment name,
then run (do not put the key itself on the command line):

```sh
opensre triage connect --name payments \
  --query-url https://your-signoz.example \
  --services payment,checkout,frontend \
  --ingress-url https://your-gateway.example \
  --api-key-env SIGNOZ_API_KEY
```

OpenSRE verifies a scoped query and shows the source-specific webhook URL,
username, and random password once. Save them in SigNoz's Webhook notification
channel with Basic auth and **Send resolved** enabled. The command also shows
the supported `/api/v1/channels` receiver JSON if you manage channels through the
API. Choose this channel on the alerts you want investigated; your existing
paging policy stays under your control. Remote live ingress requires your own
HTTPS endpoint. Localhost HTTP works only when SigNoz can actually reach that
host; localhost inside a container points to that container.

Use SigNoz's channel Test and `/triage status` to check delivery. Query readiness,
webhook delivery, and gateway-worker liveness are separate status fields. The
native SigNoz test notification confirms delivery and does not create an incident
or spend a model budget. Then verify a genuine alert reaches `/triage list`.

## Reports, follow-ups, and controls

- `/triage show <occurrence-id>` shows the current alert lifecycle, all report
  revisions, query windows/payloads, and separate stored evidence.
- `/triage ask <occurrence-id> "Which evidence is missing?"` queues a restricted
  follow-up using the same source/service authority and a new bounded budget.
- `/triage pause <source-id>` records incoming lifecycle updates, cancels active
  work cooperatively, and skips new automatic investigations.
- `/triage resume <source-id>` permits future occurrences; it does not replay
  paused backlog or reinvestigate a duplicate firing notification.
- `/triage remove <source-id>` revokes local authority and deletes its query-key
  reference while retaining accepted events/reports. Remove its channel on your
  SigNoz side when you no longer want delivery retries.

Reports distinguish observed evidence, likely cause, unknowns, and the next
check. Missing telemetry never proves health. Provider errors and budget stops
produce honest partial/failed findings. Token usage is recorded when the provider
reports it; unavailable token/cost information is displayed as unknown.

Native grouped notifications are persisted atomically before acknowledgement.
Occurrence identity is `(trusted source, fingerprint, canonical startsAt)`;
repeats update counts, resolution is monotonic, and a new startsAt is a new
occurrence. A resolved notification can update an existing completed report's
lifecycle without rewriting the report. Overload returns HTTP 503 with Retry-After
so SigNoz can retry; no part of that batch is acknowledged or committed.

A worker shares the gateway turn-capacity gate and runs one investigation at a
time. Each claim has an original 180-second deadline, 12 tool calls, 8 actual
provider invocations (including any final handoff), at most 50 rows/query, and an
event-anchored window clipped to 20 minutes and never exceeding one hour. Deadline
and pause cancellation are cooperative at provider/tool boundaries; an in-flight
provider call must return before the physical turn slot is released. A crashed
worker's expired lease gets at most one retry within the **original** deadline
and remaining call budget. Fenced old owners cannot overwrite evidence/reports.

Only three explicitly scoped SigNoz read tools are exposed. There is no shell,
MCP discovery, remediation, memory-writing, or alert-configuration tool. Incoming
labels, annotations, receiver names, URLs, and follow-up text cannot change the
trusted source URL, credential, services, or evidence window.

Unread lifecycle notifications appear in the shell without taking over the
prompt. On restart the shell summarizes stored unread events. Use `/triage list`
and `/triage show` for the durable record.

## Official contracts used

The implementation is checked against the SigNoz v0.145.0 release source:

- [Webhook documentation](https://signoz.io/docs/alerts-management/notification-channel/webhook/): native Alertmanager grouped webhook v4, Basic auth, fingerprint,
  startsAt/endsAt and resolved delivery.
- [Query Range v5 types and request fixtures](https://github.com/SigNoz/signoz/tree/v0.145.0/pkg/types/querybuildertypes/querybuildertypesv5): epoch **milliseconds**, builder-query envelopes, `service.name` and `hasError` filters.
- [v1 channel routes](https://github.com/SigNoz/signoz/blob/v0.145.0/pkg/apiserver/signozapiserver/alertmanager.go) and [legacy receiver handler](https://github.com/SigNoz/signoz/blob/v0.145.0/pkg/alertmanager/signozalertmanager/handler.go): `/api/v1/channels` accepts receiver JSON (`webhook_configs`);
  the newer `/api/v2/notification_channels` has a different config envelope and
  paginated list shape and is not substituted for v1.
- [Service-account routes](https://github.com/SigNoz/signoz/blob/v0.145.0/pkg/apiserver/signozapiserver/serviceaccount.go): service accounts, viewer roles, and API keys.
- [Session types](https://github.com/SigNoz/signoz/tree/v0.145.0/pkg/types/authtypes) and [registration handler](https://github.com/SigNoz/signoz/blob/v0.145.0/pkg/query-service/app/http_handler.go): fresh demo registration, organization lookup and email/password session.
- [Alert rule types](https://github.com/SigNoz/signoz/blob/v0.145.0/pkg/types/ruletypes/api_params.go) and [validation fixtures](https://github.com/SigNoz/signoz/blob/v0.145.0/pkg/types/ruletypes/validate_test.go): v1 rule schema with v5 composite-query builder, count of payment error spans, one-minute evaluation, and an owned preferred channel.
- [Supported Docker installation](https://signoz.io/docs/install/docker/) and [Foundry release](https://github.com/SigNoz/signoz-foundry/releases/tag/v0.3.0): generated installation rather than deprecated legacy deployment Compose.

SigNoz releases can change these API schemas independently. Live connection does
not provision admin resources, and any future demo upgrade should update the
release pins, source-backed request fixtures, and real-stack E2E verification
together.
