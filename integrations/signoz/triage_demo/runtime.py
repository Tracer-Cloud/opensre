"""Resumable paymentFailure demo; real native notifications are never simulated."""

from __future__ import annotations

import json
import secrets
import shutil
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from filelock import FileLock

from config.constants.paths import opensre_home
from config.constants.triage_demo import (
    DEMO_CREDENTIAL_SUFFIXES,
    DEMO_DISK_BYTES,
    DEMO_PREFIX,
    DEMO_RAM_BYTES,
    demo_credential_ref,
)
from config.llm_credentials import delete_credential, resolve_env_credential, save_credential
from core.domain.alerts.triage.storage import TriageStore
from integrations.signoz.client import SigNozClient
from integrations.signoz.config import SigNozConfig
from integrations.signoz.triage_credentials import managed_credentials_required
from integrations.signoz.triage_demo.artifacts import prepare, run
from integrations.signoz.triage_demo.compose import compose, generate, write_compose
from integrations.signoz.triage_demo.provision import DemoAdmin
from integrations.signoz.triage_setup import connect_source


def free_port() -> int:
    """Choose a currently available loopback port, rechecked by Docker on bind."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def demo_root(identifier: str) -> Path:
    """Accept names, never arbitrary caller-controlled filesystem paths."""
    if not identifier.startswith(DEMO_PREFIX) or not identifier[len(DEMO_PREFIX) :].isalnum():
        raise ValueError("Invalid demo ID")
    return opensre_home() / "alerts" / "demos" / identifier


def preflight(root: Path) -> None:
    """Check existing Docker/Compose, Docker RAM, disk; never install system software."""
    if managed_credentials_required():
        raise ValueError(
            "Run the disposable demo from a local OpenSRE installation with credential storage enabled"
        )
    run(["docker", "compose", "version"], root, timeout=15)
    info = json.loads(run(["docker", "info", "--format", "{{json .}}"], root, timeout=15))
    if int(info.get("MemTotal", 0)) < DEMO_RAM_BYTES:
        raise ValueError("Docker needs at least 8 GiB RAM for SigNoz plus the application")
    if shutil.disk_usage(root).free < DEMO_DISK_BYTES:
        raise ValueError("Demo needs at least 15 GiB free disk space")


def wait_for(
    check: Callable[[], Any], *, seconds: float, message: str, progress: Callable[[str], None]
) -> Any:
    """Bound real readiness/lifecycle waits and keep progress visible."""
    deadline, last_notice = time.monotonic() + seconds, 0.0
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (httpx.HTTPError, RuntimeError):
            pass
        if time.monotonic() - last_notice > 20:
            progress(message)
            last_notice = time.monotonic()
        time.sleep(2)
    raise TimeoutError(message + "; timed out. Inspect demo status and resume with its ID")


class PaymentDemo:
    """Own only manifest-listed containers; retain reports across reset/cleanup."""

    def __init__(
        self, identifier: str | None = None, *, progress: Callable[[str], None] = print
    ) -> None:
        self.id = identifier or DEMO_PREFIX + secrets.token_hex(6)
        self.root = demo_root(self.id)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.path = self.root / "manifest.json"
        self.progress = progress
        self.store = TriageStore()
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data.get("id") != self.id or self.data.get("schema") != 1:
                raise ValueError("Demo manifest identity/schema mismatch")
        else:
            self.data = {
                "id": self.id,
                "schema": 1,
                "stage": "created",
                "signoz_port": free_port(),
                "app_port": free_port(),
                "created_at": time.time(),
            }
            self.save()

    def save(self, stage: str | None = None) -> None:
        """Atomically checkpoint progress without credentials in the manifest."""
        if stage:
            self.data["stage"] = stage
        self.data["updated_at"] = time.time()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.path)
        if stage:
            self.progress(f"{self.id}: {stage}")

    def status(self) -> dict[str, Any]:
        """Read persisted progress, endpoints, report ID, and diagnostic state."""
        return dict(self.data)

    @property
    def signoz_url(self) -> str:
        return f"http://127.0.0.1:{self.data['signoz_port']}"

    @property
    def app_url(self) -> str:
        return f"http://127.0.0.1:{self.data['app_port']}"

    def flag(self, enabled: bool) -> None:
        """Write the mounted flag directory atomically; ensure raw PII stays disabled."""
        application = Path(self.data["application"])
        if not application.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Application path is outside this demo")
        path = application / "src" / "flagd" / "demo.flagd.json"
        flags = json.loads(path.read_text())
        flags["flags"]["paymentFailure"]["defaultVariant"] = "100%" if enabled else "off"
        flags["flags"]["emitRawPii"]["defaultVariant"] = "off"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(flags, indent=2))
        temporary.replace(path)

    def checkout(self) -> bool:
        """Exercise a synthetic cart and checkout using upstream's test-person fixture."""
        user = "opensre-triage-demo-" + secrets.token_hex(4)
        fixture = Path(self.data["application"]) / "src" / "load-generator" / "people.json"
        person = json.loads(fixture.read_text())[0]
        person["email"] = "synthetic-demo@example.invalid"
        with httpx.Client(base_url=self.app_url, timeout=15) as client:
            added = client.post(
                "/api/cart",
                json={"userId": user, "item": {"productId": "0PUK6V6EV0", "quantity": 1}},
            )
            added.raise_for_status()
            response = client.post("/api/checkout", json={**person, "userId": user})
            return response.is_success and bool(response.json().get("orderId"))

    def start(self, ensure_gateway: Callable[[], int]) -> dict[str, Any]:
        """Prepare, prove baseline telemetry, trigger fault, wait for a genuine report."""
        with FileLock(str(self.root / "operation.lock"), timeout=0):
            if self.data["stage"] == "cleaned":
                raise ValueError(
                    "Demo cleaned; start a new demo rather than reusing revoked authority"
                )
            if self.data["stage"] in {"report_ready", "resolved"}:
                return self.status()
            if self.data["stage"] == "resetting":
                return self._reset()
            self.data.pop("failure", None)
            preflight(self.root)
            gateway_port = ensure_gateway()
            if (
                self.data["stage"] in {"created", "downloaded"}
                or not self.data.get("application")
                or not self.data.get("image_digests")
                or not all(
                    (self.root / filename).exists()
                    for filename in ("signoz.compose.json", "application.compose.json")
                )
            ):
                binary, application = prepare(self.root)
                self.data["application"] = str(application)
                self.save("downloaded")
                paths, pins = generate(
                    self.root,
                    binary,
                    application,
                    self.id,
                    self.data["signoz_port"],
                    self.data["app_port"],
                )
                # SigNoz reaches the host's authenticated native endpoint on Linux/Desktop.
                spec = json.loads(paths[0].read_text())
                spec["services"][f"{self.id}-signoz-0"]["extra_hosts"] = [
                    "host.docker.internal:host-gateway"
                ]
                write_compose(paths[0], spec)
                self.data.update(application=str(application), image_digests=pins)
                self.flag(False)
                self.save("prepared")
            for filename in ("signoz.compose.json", "application.compose.json"):
                compose(self.root, self.id, self.root / filename, "up", "-d")
            wait_for(
                lambda: httpx.get(self.signoz_url + "/api/v1/health", timeout=5).is_success,
                seconds=600,
                message="Waiting for SigNoz readiness",
                progress=self.progress,
            )
            admin = DemoAdmin(self.signoz_url, self.id)
            admin.login()
            key = admin.query_key()
            if not any(s.id == self.id for s in self.store.sources()):
                ref = demo_credential_ref(self.id, "WEBHOOK_PASSWORD")
                password = resolve_env_credential(ref) or secrets.token_urlsafe(32)
                save_credential(ref, password)
                source, _ = connect_source(
                    self.store,
                    name=self.id,
                    query_url=self.signoz_url,
                    api_key=key,
                    services=("payment", "checkout", "frontend"),
                    ingress_url=f"http://host.docker.internal:{gateway_port}",
                    demo=True,
                    source_id=self.id,
                    webhook_password=password,
                )
            else:
                source = self.store.source(self.id)
                if (
                    source.removed
                    or source.paused
                    or not source.demo
                    or source.query_url != self.signoz_url
                ):
                    raise ValueError("Demo source changed; inspect it before resuming")
            password = resolve_env_credential(demo_credential_ref(self.id, "WEBHOOK_PASSWORD"))
            if not password:
                raise ValueError("Saved demo webhook credential is unavailable")
            admin.alerts(source.webhook_url, source.username, password)
            client = SigNozClient(SigNozConfig(url=self.signoz_url, api_key=key))
            if "fault_at" not in self.data:
                wait_for(
                    self.checkout,
                    seconds=300,
                    message="Waiting for a healthy synthetic checkout",
                    progress=self.progress,
                )
                wait_for(
                    lambda: client.query_traces(service="payment", limit=5).get("traces"),
                    seconds=180,
                    message="Waiting for queryable baseline payment telemetry",
                    progress=self.progress,
                )
                self.save("baseline_verified")
                self.data["fault_at"] = time.time()
                self.save("enabling_fault")
            self.flag(True)
            self.save("fault_enabled")

            def occurrence() -> str | None:
                # Generate additional real checkouts while awaiting provider evaluation.
                self.checkout()
                return next(
                    (
                        r["id"]
                        for r in self.store.list(limit=1000)
                        if r["source_id"] == self.id and r["first_seen"] >= self.data["fault_at"]
                    ),
                    None,
                )

            identifier = self.data.get("occurrence_id") or wait_for(
                occurrence,
                seconds=600,
                message="Waiting for SigNoz to deliver the native firing alert",
                progress=self.progress,
            )
            self.data["occurrence_id"] = identifier
            self.save("alert_received")
            wait_for(
                lambda: any(j["report"] for j in self.store.show(identifier)["investigations"]),
                seconds=240,
                message="Waiting for the gateway's automatic report",
                progress=self.progress,
            )
            self.save("report_ready")
            return self.status()

    def reset(self) -> dict[str, Any]:
        """Disable the fault, prove checkout recovery, observe native resolved update."""
        with FileLock(str(self.root / "operation.lock"), timeout=0):
            return self._reset()

    def _reset(self) -> dict[str, Any]:
        """Resume recovery while the caller holds the operation lock."""
        if self.data["stage"] == "cleaned":
            raise ValueError("Demo cleaned; start a new demo to run another investigation")
        if "application" not in self.data or "fault_at" not in self.data:
            raise ValueError(
                "Demo has not enabled its fault; resume setup with triage demo --id " + self.id
            )
        self.flag(False)
        self.save("resetting")
        wait_for(
            self.checkout,
            seconds=180,
            message="Waiting for checkout recovery",
            progress=self.progress,
        )
        identifier = self.data.get("occurrence_id")
        if identifier:
            wait_for(
                lambda: self.store.show(identifier)["lifecycle"] == "resolved",
                seconds=600,
                message="Waiting for the native resolved notification",
                progress=self.progress,
            )
        self.save("resolved")
        return self.status()

    def cleanup(self) -> dict[str, Any]:
        """Down only validated owned Compose resources; keep durable reports."""
        with FileLock(str(self.root / "operation.lock"), timeout=0):
            if "application" in self.data:
                self.flag(False)
            for filename in ("application.compose.json", "signoz.compose.json"):
                path = self.root / filename
                if path.exists():
                    compose(self.root, self.id, path, "down", "--volumes")
            for source in self.store.sources():
                if source.id == self.id and source.demo:
                    if not source.removed:
                        self.store.control(source.id, "remove")
                    if source.credential_kind == "owned":
                        delete_credential(source.credential_ref)
            for suffix in DEMO_CREDENTIAL_SUFFIXES:
                delete_credential(demo_credential_ref(self.id, suffix))
            self.save("cleaned")
            return self.status()
