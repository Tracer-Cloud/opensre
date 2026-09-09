---
name: querying-yandex-cloud
description: >
  Read Yandex Cloud through its REST API. Applies to any question about VMs,
  metrics, logs, audit events, Kubernetes, managed databases, serverless,
  networking or any other Yandex Cloud resource. Never shell out to the `yc`
  CLI — it is not how this agent reaches Yandex Cloud and is usually not
  installed.
tools:
  - find_yc_api
  - execute_yc_operation
---

# querying-yandex-cloud

Three rules first, because they are the ones that waste a whole investigation
when they are missed.

**Read `kubernetes_get_events` before naming a cause** for any pod that is not
Running. The pod list shows only *that* it is stuck. Every reason looks
identical there — out of memory, unschedulable, an image that will not pull, a
volume that will not mount. The event carries the actual message, and guessing
between them produces a confident wrong answer that sends someone to check node
capacity when the registry was refusing the pull. `Pending` with no events about
resources is an image or volume problem, not a scheduling one.

**A pod is not a Yandex Cloud resource.** The Yandex Cloud API knows the
Kubernetes *cluster* — version, health, node groups, read with
`execute_yc_operation` on `/managed-kubernetes/` — and nothing about what runs
inside it. Neither `execute_yc_operation` nor `find_yc_api` can reach a pod, an
event or a container log, and no `/managed-kubernetes/` path returns one. Read
those with `kubernetes_list_pods`, `kubernetes_get_events`,
`kubernetes_get_pod_logs` and `kubernetes_list_nodes`, which talk to the
cluster's own API server. If those tools are absent, Managed Kubernetes access
was not connected during setup — say so rather than trying the Yandex Cloud API
instead.

**Never run `yc` via a shell tool.** It is normally not installed, it needs its
own separate authentication, and it can mutate. If you catch yourself writing
`yc ...` to answer a question, use `execute_yc_operation` instead.

**But do write the `yc ...` command out when something should change.** These
tools only read. When the finding calls for an action — resize, restart,
rebalance, change a setting — end with the exact `yc ...` command an operator
can paste, not a description of what to do. Writing the command is correct;
running it is not.

Everything else reaches Yandex Cloud over its REST API with the configured
credential. There is no CLI step and no shell step.

## What a connected `yandex_cloud` means

`yandex_cloud` appears once in the connected-integrations list, but it is an
umbrella. When it is connected you can already read **all** of this — do not
tell the user a piece of it "is not configured":

| Ask about | Reach it with |
| --- | --- |
| Metrics, CPU, memory, disk, saturation | `query_yc_metrics`, `list_yc_metrics` |
| Logs (Cloud Logging) | `read_yc_logs`, `list_yc_log_groups` |
| Logs of a managed database | `read_yc_db_logs` — a separate store, see below |
| Audit events, who changed what | not readable yet — audit trails write to a sink |
| VMs, disks, images, instance groups | `list_yc_instances`, `get_yc_instance_diagnostics`; otherwise `execute_yc_operation` |
| Load balancer target health | `get_yc_lb_health` |
| Kubernetes **clusters and node groups** | `execute_yc_operation` on `/managed-kubernetes/` |
| Kubernetes **pods, events, pod logs, nodes** | `kubernetes_list_pods`, `kubernetes_get_events`, `kubernetes_get_pod_logs`, `kubernetes_list_nodes` |
| Managed PostgreSQL/MySQL/ClickHouse/Valkey/StoreDoc/Kafka/OpenSearch | `list_yc_db_clusters`, `get_yc_db_cluster` |
| Functions, containers, triggers, API gateways | `execute_yc_operation` |
| Networks, subnets, security groups | `execute_yc_operation` |
| Anything else in the API | `find_yc_api`, then `execute_yc_operation` |
| Which services exist at all | `find_yc_api` with no query |

Monitoring is configured whenever Yandex Cloud is configured — it shares one
credential. Cloud Logging *group* lists use that same REST credential.
Reading **log entries** needs the optional install extra
`opensre[yandex_cloud_logs]` (gRPC stubs for the log reader host). Without it,
`read_yc_logs` fails closed and tells you to install the extra; do not tell
the user logging "is not configured" when the umbrella integration is connected
and only the entry-reader extra is missing.


## Reading anything

`find_yc_api` indexes ~940 read endpoints across 68 services, generated from
Yandex's own protobuf definitions. It is the answer to "what is the path for
X", so use it rather than guessing:

1. `find_yc_api` with the resource in plain words — `security groups`,
   `postgresql clusters`, `node group`, `certificates`.
2. `execute_yc_operation` with the `service` and `path` it returned.
3. A path with a `{placeholder}` needs an id. Call the list endpoint on the
   same service first and take the id from there.

Reads only. Every mutating operation uses a different HTTP verb and the client
refuses those, so there is no way to change anything here. When the answer is
that something *should* change, give the user the exact `yc ...` command to run
themselves — writing that command out is correct, running it is not.

## Kubernetes workloads are not in the Yandex Cloud API

The Yandex Cloud API knows about the *cluster* — its version, health, node
groups, maintenance window. It knows nothing about what runs inside it. There is
no endpoint for pods, and searching for one wastes the investigation:

> A pod is not a Yandex Cloud resource. `execute_yc_operation` and `find_yc_api`
> cannot reach one, and neither can any `/managed-kubernetes/` path.

Workloads are read with the `kubernetes_*` tools, which talk to the cluster's own
API server:

| Ask about | Reach it with |
| --- | --- |
| Which pods exist, and their state | `kubernetes_list_pods` |
| Why a pod is not starting | `kubernetes_describe_pod`, `kubernetes_get_events` |
| What a container logged | `kubernetes_get_pod_logs` |
| Node pressure, NotReady nodes | `kubernetes_list_nodes` |
| Deployments, services, statefulsets, ingresses | `kubernetes_list_deployments` and friends |
| Anything else in the cluster | `kubernetes_get_resource` |

So an incident in a Managed Kubernetes cluster is usually two reads, in this
order:

1. `execute_yc_operation` on `/managed-kubernetes/v1/clusters` — is the control
   plane itself healthy? A degraded master explains everything below it, and
   nothing else needs checking.
2. `kubernetes_list_pods` and `kubernetes_get_events` — what is actually wrong
   with the workload.

These tools appear only when a cluster was connected during setup. If they are
absent, say that Managed Kubernetes access is not configured and that
`opensre integrations setup kubernetes` connects it — do not fall back to
`execute_yc_operation` and report that pods cannot be read.

A pod stuck in `Pending` has no logs at all: the container never started, so the
API answers `400`, not `403`. Read its events instead — that is where the reason
is.

## Logs live in four different places

There is no single log store, so **an empty `read_yc_logs` is a statement about
Cloud Logging and nothing else.** Where to look depends on who wrote the log:

| Written by | Read with | Reaches Cloud Logging? |
| --- | --- | --- |
| A managed database engine | `read_yc_db_logs` | Only if export was switched on |
| A container in Kubernetes | `kubernetes_get_pod_logs` | Only if a log agent was deployed |
| A serverless function or container | `read_yc_logs` | Yes, by default |
| Your own application, sending to Cloud Logging | `read_yc_logs` | Yes, that is what it is |

So the search order for "why did this break" is: ask the owning service first,
then Cloud Logging. Doing it the other way round produces a confident "no logs
found" for a database that has been logging all along.

A managed database keeps several streams and serves one at a time, so name
the one the question is about: MySQL answers with its error log unless
`MYSQL_SLOW_QUERY` is asked for, and a question about slow queries otherwise
gets a confident empty answer. The result lists the other streams.

Export to Cloud Logging is optional and off unless someone enabled it. If a
managed service has no log group, that is a configuration fact worth reporting —
not evidence the service is silent.

**Before saying something cannot be read, ask `find_yc_api`.**

Never answer by telling the user to install or run the `yc` CLI. If the CLI can
read it, so can this agent — the CLI is a client of the same REST API, and the
path is in the index.

## A past incident needs an explicit window

Every reader defaults to a window ending now. Asked about an incident on a given
date, **pass that date as the window that reader actually accepts** —
`query_yc_metrics` takes `from_time` and `to_time`; `read_yc_logs` takes
`since` and `until`. `window_minutes` counts back from the present and will
silently return nothing for anything older, which reads as "no evidence"
rather than "wrong window".

Retention differs by source, and an empty result means different things:

| Source | Kept | An empty result means |
| --- | --- | --- |
| Cloud Logging (`read_yc_logs`) | ~31 days | Beyond retention if the date is older — say so, do not call it "no evidence" |
| Managed-database logs (`read_yc_db_logs`) | per cluster | A separate store from Cloud Logging, so Cloud Logging's silence says nothing about it |
| Monitoring (`query_yc_metrics`) | months | Genuinely no data for that window, if the window was right |

So for an incident weeks back, metrics are usually the only surviving evidence,
and they are worth querying with the exact window before concluding anything.
Cluster operation history (`/managed-*/v1/clusters/{id}/operations`) outlives
logs too and shows maintenance and failovers.

## Answering well

- **Query first, then answer.** Infrastructure questions are answered from live
  reads, never from what you remember about Yandex Cloud in general.
- **Say what you actually read.** Name the folder, the resource ids, the time
  window. "CPU on `build-vm` averaged 140% over the last hour (2 cores)" beats
  "CPU looks high".
- **An empty list is an answer.** "No managed database clusters in this folder"
  is a finding; do not present it as a failure or a missing integration.
- **One folder at a time.** Every read is scoped to the configured folder.
  Reading a different one means passing `folderId` explicitly to
  `execute_yc_operation`; the folder list is at
  `/resource-manager/v1/folders` with `cloudId`.
- **A bare 404 usually means a wrong parameter**, not a missing resource — a
  single-resource read rejects `folderId` and `pageSize`. Re-read the path shape
  before telling the user the resource does not exist.

## Metrics

`query_yc_metrics` takes a Yandex Monitoring query: `"cpu_usage"{service="compute"}`,
narrowed by labels such as `resource_id`, `device`, `subcluster_name`.

**It is not PromQL.** `sum by (...)`, `rate(x[5m])` and `topk(...)` come back as
parse errors, not empty results. Aggregate with Yandex's own functions:

| Want | Write |
| --- | --- |
| Total across series | `series_sum("metric"{...})` |
| Average across series | `series_avg("metric"{...})` |
| Highest n series | `top_max(5, "metric"{...})` |
| Drop gaps | `drop_nan("metric"{...})` |
| Name the result | `alias(..., "label")` |

**The folder label is `folder_id`.** Writing `folderId` is accepted and matches
nothing, so it looks like the metric has no data rather than like a mistake.

**Metrics the user pushes themselves live under `service="custom"`, and the
default metric listing does not include them.** So `list_yc_metrics` returning
nothing is not evidence a metric is missing — search again with
`selectors='service="custom"'` before saying it does not exist. The tool says so
in its own output when a search comes back empty; believe it rather than
repeating the same call with a different name filter.
