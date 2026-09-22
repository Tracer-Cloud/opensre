## Deployment

OpenSRE has an OpenSRE Cloud managed gateway, an AWS EC2 path, and a general
hosted runtime option for ASGI-compatible platforms:

- **OpenSRE Cloud managed gateway** — an organization admin provisions it in
  the web app. Signed-in shell users can check, start, or stop their
  organization's gateway; start and stop require approval and admin access.
- **Slack** — deployed and operated separately, not from this repo. The EC2
  path below never ships `SLACK_*` variables (Socket Mode is single-consumer —
  a second consumer would split events).
- **Telegram** — the EC2 gateway deploy below.

---

## Gateway Deploy — AMI + systemd (Telegram)

Runs the Telegram gateway directly on EC2 as a systemd service. The gateway is
baked into a custom AMI once; subsequent deploys launch from that AMI in ~2–3
minutes.

**Prerequisites:** AWS credentials with EC2 / IAM / SSM permissions. No Docker needed.

Copy [`.env.deploy.example`](.env.deploy.example) and export the required variables:

| Variable | Required | Used by |
| -------- | -------- | ------- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway service |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |
| `LLM_PROVIDER` + API key | Yes | Gateway service |

`SLACK_*` variables are ignored by the EC2 deploy (validation warns) — Slack is deployed and operated separately, not from this repo.

```bash
# Step 1 — bake a gateway AMI (run once per code change, takes ~5-10 minutes):
make build-gateway-image

# Step 2 — launch EC2 instance from the saved AMI (fast):
make deploy-gateway

# Tear down (keeps AMI by default):
make destroy-gateway

# Full teardown including AMI deregistration:
OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1 make destroy-gateway
```

Rollback to a previously baked AMI:

```bash
OPENSRE_GATEWAY_AMI_ID=ami-<previous-id> make deploy-gateway
```

Check the running gateway via SSM:

```bash
aws ssm start-session --target <InstanceId>
# inside:
sudo systemctl status opensre-gateway
sudo journalctl -u opensre-gateway -f
```

Outputs are written to `~/.opensre/deployments/opensre-gateway.json`.

After deploy, the web API is reachable publicly:

```bash
curl http://<PublicIpAddress>:8000/health
```

Restrict the allowed source CIDR with `OPENSRE_WEB_API_INGRESS_CIDR` (default `0.0.0.0/0`).

### Direct deploy (no pre-baked AMI)

Installs OpenSRE inline on a fresh EC2 instance via SSM — slower but requires no bake step:

```bash
make install-gateway-on-new-server
make destroy-gateway-on-new-server
```

---

## Runtime Environment (Hosted / General)

Deploy OpenSRE as a standard Python/FastAPI app using the repo `Dockerfile`, Railway,
ECS, Vercel, or another ASGI-capable host.

1. Build and deploy using your hosting provider's normal workflow.
2. Set `LLM_PROVIDER` and the matching provider API key:
    - `ANTHROPIC_API_KEY` when `LLM_PROVIDER=anthropic`
    - `OPENAI_API_KEY` when `LLM_PROVIDER=openai`
    - `OPENROUTER_API_KEY` when `LLM_PROVIDER=openrouter`
    - `TRUSTEDROUTER_API_KEY` when `LLM_PROVIDER=trustedrouter`
    - `GEMINI_API_KEY` when `LLM_PROVIDER=gemini`
3. Set `DATABASE_URL` when the gateway needs shared Postgres-backed state. It
   is required for Slack Events API unless you deliberately use the
   single-replica local-dedup escape hatch.
4. Put `OPENSRE_HOME` on a shared mount before running multiple processes that
   need the same scheduler tasks. If `OPENSRE_CONTEXT_ROOT` is set, make that
   root durable and shared for organization-bound sessions too.
5. Add any additional environment variables required by your integrations.

Minimum environment:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
```

The full set of supported provider keys and optional model overrides is documented in
[`.env.example`](.env.example).

### Railway

Add Railway Postgres and set `DATABASE_URL` when you use Slack Events API or
another layout that needs gateway records shared between replicas. Redis is
not part of the gateway persistence path. Scheduler files still need a shared
`OPENSRE_HOME` mount. Organization-bound sessions use `OPENSRE_CONTEXT_ROOT`
when set and otherwise fall back to `OPENSRE_HOME`; make both durable when they
differ.

For telemetry labeling, set `OPENSRE_DEPLOYMENT_METHOD=railway` on the Railway service.

---
