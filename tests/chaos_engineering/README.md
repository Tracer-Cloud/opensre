# Chaos engineering + Datadog (kind)

**Defaults:** cluster `tracer-k8s-test` · context `kind-tracer-k8s-test` · Datadog Helm namespace `tracer-test`.

This directory holds Kubernetes manifests (`chaos-demo.yaml`, `pod-kill-demo.yaml`, `experiments/<name>/`), JSON samples for `opensre investigate -i`, and the Python helpers behind **`make chaos-lab-*`** / **`make chaos-experiment-*`** (`python -m tests.chaos_engineering`). Alert paths in JSON use the repo-relative form `tests/chaos_engineering/experiments/.../foo-alert.json`.

## Quick start (recommended)

From **repo root**: Docker, `kind`, `kubectl`, `helm`, and a venv with the repo installed (`pip install -e ".[dev]"`).

```bash
export DD_API_KEY='your-api-key'   # omit if using CHAOS_LAB_FLAGS=--skip-datadog
# export KUBECTL_CONTEXT=kind-tracer-k8s-test   # optional

make chaos-lab-up

make chaos-experiment-list
make chaos-experiment-up EXPERIMENT=pod-failure
make chaos-experiment-down EXPERIMENT=pod-failure

make chaos-lab-down
```

**Flags (via Make):** `CHAOS_LAB_FLAGS='--skip-datadog'`, `'--skip-kind'`, `'--no-wait-datadog'`. **Teardown:** `CHAOS_LAB_DOWN_FLAGS='--keep-kind'` or `'--keep-datadog'`.

**Debugging (same as Make):** `python -m tests.chaos_engineering lab up`, `experiment list`, etc.

**Convention:** Each experiment uses sorted `*-demo.yaml` then `*-chaos.yaml`. On delete, chaos CRs first (`make chaos-experiment-down`).

**Baselines:** `make chaos-engineering-apply` applies `chaos-demo`, crashloop workload, and `pod-kill-demo` PodChaos only. Other scenarios use `make chaos-experiment-up EXPERIMENT=<name>` or raw `kubectl` (workload before chaos; delete chaos before workload if doing it manually).

**Discover experiments:** `make chaos-experiment-list` (or browse `experiments/`). Examples include network faults (`network-delay`, `network-partition`, …), `crashloop`, `pod-failure`, `stress-cpu`, `dns-error`, `http-abort`, and others.

| Example directory | Chaos Mesh (illustrative) |
| --- | --- |
| `experiments/network-delay/` | `NetworkChaos` delay |
| `experiments/crashloop/` | CrashLoopBackOff + optional PodChaos |
| `experiments/pod-failure/` | `PodChaos` pod-failure (pause image) |
| `experiments/dns-error/` | `DNSChaos` (needs Chaos DNS Server) |

## Manual setup (without `make chaos-lab-up`)

Use this if you want to run the same steps by hand. Sections 1–2 mirror what `chaos-lab-up` does before Chaos Mesh and baselines.

### 1. Cluster

```bash
kind create cluster --name tracer-k8s-test --wait 60s
kubectl config use-context kind-tracer-k8s-test
```

### 2. Datadog

```bash
export DD_API_KEY='your-api-key'
# export DD_SITE=datadoghq.eu   # if not US1

kubectl create namespace tracer-test --context=kind-tracer-k8s-test --dry-run=client -o yaml \
  | kubectl apply -f - --context=kind-tracer-k8s-test

helm repo add datadog https://helm.datadoghq.com
helm repo update datadog

helm upgrade --install datadog datadog/datadog \
  --kube-context kind-tracer-k8s-test \
  -n tracer-test \
  -f tests/e2e/kubernetes/k8s_manifests/datadog-values.yaml \
  --set "datadog.apiKey=${DD_API_KEY}" \
  ${DD_SITE:+--set datadog.site=${DD_SITE}} \
  --wait --timeout 10m
```

Confirm a node Agent exists (`kubectl get daemonset,pods -n tracer-test --context=kind-tracer-k8s-test`). If there is no agent DaemonSet, fix `datadog-values.yaml` (e.g. `datadog.operator.enabled: false`) and upgrade again.

### 3. Chaos Mesh + baseline workloads

```bash
make chaos-mesh-up
kubectl get pods -n chaos-mesh --context=kind-tracer-k8s-test
make chaos-engineering-apply
kubectl get pods -n default --context=kind-tracer-k8s-test
```

Optional extra PodChaos on crashloop: `make chaos-experiment-up EXPERIMENT=crashloop`, or apply `experiments/crashloop/pod-kill-crashloop-chaos.yaml` after the crashloop demo workload is up.

**Pod-failure** (not part of `chaos-engineering-apply`): `make chaos-experiment-up EXPERIMENT=pod-failure`, or apply `experiments/pod-failure/*-demo.yaml` then `*-chaos.yaml`. Expect **0/1 READY** while chaos is active (pause image). Teardown: delete chaos CR first.

### 4. Datadog logs

After the crashloop pod runs, wait a few minutes, then query logs e.g. `kube_cluster_name:tracer-k8s-test kube_namespace:default crashloop-demo` or `kube_deployment:crashloop-demo`.

### 5. OpenSRE

```bash
opensre onboard   # Datadog keys as needed
opensre investigate -i tests/chaos_engineering/experiments/crashloop/crashloop-demo-alert.json
```

Use the matching `*-alert.json` under `experiments/<name>/` for other scenarios. Local kind is observed via Datadog (or a richer synthetic payload), not EKS APIs.

## Cleanup

```bash
make chaos-engineering-delete
make chaos-mesh-down
helm uninstall datadog -n tracer-test --kube-context kind-tracer-k8s-test
kubectl delete namespace tracer-test --context=kind-tracer-k8s-test
kind delete cluster --name tracer-k8s-test
```

Or use `make chaos-lab-down` (and flags) if you brought the lab up with `chaos-lab-up`.

**Make targets:** `chaos-lab-up`, `chaos-lab-down`, `chaos-experiment-list`, `chaos-experiment-up`, `chaos-experiment-down`, `chaos-mesh-up`, `chaos-mesh-down`, `chaos-engineering-apply`, `chaos-engineering-delete`. Override context with `KUBECTL_CONTEXT=...` when needed.
