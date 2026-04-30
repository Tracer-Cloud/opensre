# Install Proxy

Cloudflare Worker for the installer domain `install.opensre.com`.

Deploy from `infra/install-proxy`:

1. Authenticate with Cloudflare:
   `npx wrangler login`
2. Deploy the Worker:
   `npx wrangler deploy`

The proxy serves installer scripts from the repository:

- `https://install.opensre.com` -> auto-detects shell from request
- `https://install.opensre.com/install.sh` -> Unix shell installer
- `https://install.opensre.com/install.ps1` -> PowerShell installer

Optional query override on the root endpoint:

- `?shell=sh`
- `?shell=powershell`
