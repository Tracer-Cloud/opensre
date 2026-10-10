"""Official SigNoz v0.145.0 admin APIs, confined to a fresh owned demo."""

from __future__ import annotations

import secrets
from typing import Any

import httpx

from config.constants.triage_demo import demo_credential_ref
from config.llm_credentials import resolve_env_credential, save_credential


class DemoAdmin:
    """Admin bootstrap is separate from the agent's viewer query credential."""

    def __init__(self, url: str, namespace: str) -> None:
        self.url, self.namespace = url, namespace
        self.token = ""

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, **kwargs: Any
    ) -> Any:
        """Never include provider responses or admin tokens in errors or reports."""
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = httpx.request(
                method, self.url + path, json=body, headers=headers, timeout=15, **kwargs
            )
            response.raise_for_status()
            return response.json().get("data")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise RuntimeError(
                f"SigNoz demo API {method} {path} failed ({type(exc).__name__})"
            ) from None

    def login(self) -> None:
        """Register once on the owned fresh stack; resume using the saved credential."""
        ref = demo_credential_ref(self.namespace, "ADMIN_PASSWORD")
        password = resolve_env_credential(ref)
        if not password:
            password = secrets.token_urlsafe(32)
            save_credential(ref, password)
        email = f"{self.namespace}@example.invalid"
        context = self.request("GET", "/api/v2/sessions/context", params={"email": email})
        if not context.get("exists"):
            self.request(
                "POST",
                "/api/v1/register",
                {
                    "name": "OpenSRE demo",
                    "email": email,
                    "password": password,
                    "orgName": self.namespace,
                    "orgDisplayName": "Disposable OpenSRE demo",
                },
            )
            context = self.request("GET", "/api/v2/sessions/context", params={"email": email})
        orgs = context.get("orgs", [])
        if len(orgs) != 1:
            raise ValueError("Demo registration did not resolve exactly one owned organization")
        session = self.request(
            "POST",
            "/api/v2/sessions/email_password",
            {"email": email, "password": password, "orgId": orgs[0]["id"]},
        )
        self.token = session["accessToken"]

    def query_key(self) -> str:
        """Grant only signoz-viewer and mint a separately saved service-account key."""
        ref = demo_credential_ref(self.namespace, "QUERY_KEY")
        name = self.namespace + "-viewer"
        accounts = self.request("GET", "/api/v1/service_accounts")
        account = next((a for a in accounts if a["name"] == name), None)
        if account is None:
            account = self.request("POST", "/api/v1/service_accounts", {"name": name})
        identifier = account["id"]
        roles = self.request("GET", "/api/v1/roles")
        viewer = next(r for r in roles if r["name"] == "signoz-viewer")
        assigned = self.request("GET", f"/api/v1/service_accounts/{identifier}/roles")
        if any(r["name"] != "signoz-viewer" for r in assigned):
            raise ValueError("Demo query account has unexpected elevated roles")
        if not assigned:
            self.request(
                "POST",
                "/api/v1/service_account_roles",
                {"serviceAccountId": identifier, "roleId": viewer["id"]},
            )
        key = resolve_env_credential(ref)
        if not key:
            # A unique key name safely recovers a crash between mint and local save.
            created = self.request(
                "POST",
                f"/api/v1/service_accounts/{identifier}/keys",
                {"name": f"triage-{secrets.token_hex(6)}", "expiresAt": 0},
            )
            key = created["key"]
            save_credential(ref, key)
        return key

    def alerts(self, webhook_url: str, username: str, password: str) -> None:
        """Create only this demo's named channel and error-span threshold rule."""
        name = self.namespace + "-webhook"
        if not any(c["name"] == name for c in self.request("GET", "/api/v1/channels")):
            self.request(
                "POST",
                "/api/v1/channels",
                {
                    "name": name,
                    "webhook_configs": [
                        {
                            "url": webhook_url,
                            "send_resolved": True,
                            "http_config": {
                                "basic_auth": {"username": username, "password": password}
                            },
                        }
                    ],
                },
            )
        rule = payment_rule(self.namespace, name)
        if not any(r.get("alert") == rule["alert"] for r in self.request("GET", "/api/v1/rules")):
            self.request("POST", "/api/v1/rules", rule)


def payment_rule(namespace: str, channel: str) -> dict[str, Any]:
    """v1 alert schema with v5 builder query, per upstream PostableRule."""
    return {
        "alert": namespace + "-payment-errors",
        "alertType": "TRACES_BASED_ALERT",
        "ruleType": "threshold_rule",
        "version": "v5",
        "schemaVersion": "v1",
        "evalWindow": "1m",
        "frequency": "1m",
        "condition": {
            "compositeQuery": {
                "queryType": "builder",
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "traces",
                            "stepInterval": "1m",
                            "aggregations": [{"expression": "count()"}],
                            "filter": {
                                "expression": "service.name = 'payment' AND hasError = true"
                            },
                        },
                    }
                ],
            },
            "selectedQueryName": "A",
            "target": 0,
            "matchType": "1",
            "op": "1",
        },
        "labels": {"severity": "warning"},
        "annotations": {"summary": "Payment error spans observed"},
        "preferredChannels": [channel],
        "disabled": False,
    }
