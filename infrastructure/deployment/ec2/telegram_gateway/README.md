# `infrastructure/deployment/ec2/telegram_gateway/`

AMI + systemd deployment path for the OpenSRE **Telegram** gateway (long
polling). Slack is deployed and operated separately, not from this repo.

This path runs the gateway process directly on the EC2 host as a systemd service,
so shell commands (like `curl`, `systemctl`, `sudo`) work normally from
inside the gateway session.

## What's here

| Path | Purpose |
| ---- | ------- |
| `systemd/opensre-gateway.service` | systemd unit file included in the server image. Reads env from `/etc/opensre/gateway.env`. |
| `stack.py` | `GatewayStack` dataclass + helpers to persist AMI id and deployment outputs under `~/.opensre/deployments/`. |
| `build_server_image.py` | `build_server_image()` — launches a temp builder EC2 instance, runs inline install commands via SSM, snapshots an AMI, and terminates the builder. |
| `install_on_new_server.py` | `install_on_new_server()` / `destroy_installed_server()` — starts a plain EC2 instance and installs the gateway on it from the published installer, with no image built first. |
| `provision.py` | `provision_gateway_via_ssm()` and `wait_for_gateway_ready()` — writes `/etc/opensre/gateway.env` and restarts the service via SSM. |
| `lifecycle.py` | CLI entrypoint: `build-server-image`, `deploy`, `destroy`, `install-on-new-server`, `destroy-installed-server` subcommands. |

## Commands

Run from the **repo root** (`make install` first).

| Command | What it does |
| ------- | ------------ |
| `make build-gateway-image` | Launch temp EC2, install OpenSRE @ current git HEAD, snapshot AMI, save AMI id locally |
| `make deploy-gateway` | Destroy any prior stack, launch EC2 from saved AMI, write env, start service |
| `make destroy-gateway` | Terminate instance, delete IAM profile/role; AMI kept by default |
| `make install-gateway-on-new-server` | Start a plain EC2 instance and install the gateway on it, no image needed |
| `make destroy-gateway-on-new-server` | Tear down the server created by the command above |

Equivalent Python entrypoints:

```bash
uv run python -m infrastructure.deployment.ec2.telegram_gateway.lifecycle build-server-image
uv run python -m infrastructure.deployment.ec2.telegram_gateway.lifecycle deploy
uv run python -m infrastructure.deployment.ec2.telegram_gateway.lifecycle destroy
uv run python -m infrastructure.deployment.ec2.telegram_gateway.lifecycle install-on-new-server
uv run python -m infrastructure.deployment.ec2.telegram_gateway.lifecycle destroy-installed-server
```

### Prerequisites

1. **AWS credentials** — static keys or role via the default boto3 chain.
2. **Permissions** — EC2, SSM, IAM for the deploy account/region. No ECR needed.
3. **Region** — defaults to `us-east-1` (same as main deploy).

### Environment variables

Copy [`.env.deploy.example`](../../../../.env.deploy.example) and set
`TELEGRAM_BOT_TOKEN`, plus `LLM_PROVIDER` and API keys used by the main deploy.

| Variable | Required | Used by |
| -------- | -------- | ------- |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Yes (or role) | Provisioning |
| `TELEGRAM_BOT_TOKEN` | Yes | Gateway service |
| `TELEGRAM_ALLOWED_USERS` | Recommended | Gateway pairing gate |

`SLACK_*` variables are ignored by this deploy path (validation warns) — Slack
is deployed and operated separately, not from this repo.

| `LLM_PROVIDER` + API key | Yes | Gateway service |
| `OPENSRE_GATEWAY_GIT_REF` | No | Git ref to install into the image (default: local HEAD SHA) |
| `OPENSRE_GATEWAY_AMI_ID` | No | Skip the image build, use an existing image id |
| `OPENSRE_GATEWAY_DESTROY_PURGE_AMI` | No | Set to `1` to also deregister AMI on destroy |
| `OPENSRE_STACK_SUFFIX` | No | Per-developer resource name suffix |

### What `make deploy-gateway` creates

One stack named `opensre-gateway`:

- **EC2** `t3.micro` in the account default VPC
- **IAM** instance profile — SSM + Bedrock (no ECR needed)
- **systemd** `opensre-gateway.service` running as the `opensre` system user

Outputs written to `~/.opensre/deployments/opensre-gateway.json`.

### Bake once, deploy many times

```bash
# Bake once per code change (takes ~5-10 minutes):
make build-gateway-image

# Fast redeploy using the saved AMI id (takes ~2-3 minutes):
make deploy-gateway
make destroy-gateway
make deploy-gateway
```

### Rollback

To roll back to a previously built image:

```bash
OPENSRE_GATEWAY_AMI_ID=ami-<previous-id> make deploy-gateway
```

### Checking the gateway

```bash
# SSH (if EC2_KEY_NAME was set) or SSM session:
aws ssm start-session --target <InstanceId>

# Inside the instance:
sudo systemctl status opensre-gateway
sudo journalctl -u opensre-gateway -f
```

## Persistence

Gateway session state lives in `/var/lib/opensre-gateway/.opensre/` on the instance EBS
root volume.  It survives service restarts and reboots, but **does not** survive a full
`make deploy-gateway` (new instance = fresh disk).  Back up
`/var/lib/opensre-gateway/.opensre/gateway/state.db` before re-deploying if session
continuity matters.
