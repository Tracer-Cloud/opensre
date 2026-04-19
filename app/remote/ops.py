"""Provider-agnostic remote post-deploy operations."""

from __future__ import annotations

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


class RemoteOpsError(RuntimeError):
    """Raised when a remote ops provider action fails."""


@dataclass(frozen=True)
class RemoteServiceScope:
    """Logical target service identity for remote operations."""

    provider: str
    project: str | None = None
    service: str | None = None


@dataclass(frozen=True)
class ServiceStatus:
    """Normalized status payload for remote service inspection."""

    provider: str
    project: str | None
    service: str | None
    deployment_id: str | None
    deployment_status: str | None
    environment: str | None
    url: str | None
    health: str
    metadata: dict[str, str]


@dataclass(frozen=True)
class RestartResult:
    """Result payload returned after restart/redeploy."""

    provider: str
    project: str | None
    service: str | None
    requested: bool
    deployment_id: str | None
    message: str


class RemoteOpsProvider(ABC):
    """Abstract provider interface for remote post-deploy operations."""

    name: str

    @abstractmethod
    def status(self, scope: RemoteServiceScope) -> ServiceStatus:
        raise NotImplementedError

    @abstractmethod
    def logs(self, scope: RemoteServiceScope, *, lines: int, follow: bool) -> None:
        raise NotImplementedError

    @abstractmethod
    def fetch_logs(self, scope: RemoteServiceScope, *, lines: int) -> str:
        """Return the last ``lines`` log entries as text for programmatic use.

        Unlike ``logs()`` which streams to stdout for interactive CLI use,
        ``fetch_logs()`` captures the output and returns it so callers can
        feed it into an investigation or other pipeline.
        """
        raise NotImplementedError

    @abstractmethod
    def restart(self, scope: RemoteServiceScope) -> RestartResult:
        raise NotImplementedError


class RailwayRemoteOpsProvider(RemoteOpsProvider):
    """Railway-backed implementation using the Railway CLI."""

    name = "railway"

    def _ensure_linked_scope(self, scope: RemoteServiceScope) -> None:
        if not scope.project:
            return

        link_cmd = ["railway", "link", "--project", scope.project, "--json"]
        if scope.service:
            link_cmd.extend(["--service", scope.service])

        link_result = subprocess.run(
            link_cmd,
            check=False,
            text=True,
            capture_output=True,
        )
        if link_result.returncode != 0:
            stderr = (link_result.stderr or "").strip()
            stdout = (link_result.stdout or "").strip()
            detail = stderr or stdout or "unknown Railway CLI error"
            raise RemoteOpsError(f"Railway command failed ({' '.join(link_cmd)}): {detail}")

    def _build_railway_cmd(self, args: list[str], *, scope: RemoteServiceScope) -> list[str]:
        cmd = ["railway"]
        if scope.service and not scope.project:
            cmd.extend(["--service", scope.service])
        cmd.extend(args)
        return cmd

    def _railway_command(
        self,
        args: list[str],
        *,
        scope: RemoteServiceScope,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        if shutil.which("railway") is None:
            raise RemoteOpsError(
                "Railway CLI is not installed. Install with: npm install -g @railway/cli"
            )

        self._ensure_linked_scope(scope)
        cmd = self._build_railway_cmd(args, scope=scope)

        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=capture_output,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or "unknown Railway CLI error"
            raise RemoteOpsError(f"Railway command failed ({' '.join(cmd)}): {detail}")
        return result

    def _read_json(self, args: list[str], *, scope: RemoteServiceScope) -> dict[str, object]:
        result = self._railway_command(args, scope=scope, capture_output=True)
        output = (result.stdout or "").strip()
        if not output:
            return {}
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RemoteOpsError(f"Failed to parse Railway JSON output: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
        raise RemoteOpsError("Unexpected Railway JSON output shape.")

    @staticmethod
    def _as_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def status(self, scope: RemoteServiceScope) -> ServiceStatus:
        data = self._read_json(["status", "--json"], scope=scope)

        deployment = data.get("deployment")
        deployment_data = deployment if isinstance(deployment, dict) else {}

        service_name = self._as_str(data.get("service")) or scope.service
        project_name = self._as_str(data.get("project")) or scope.project
        deployment_id = self._as_str(deployment_data.get("id"))
        deployment_status = self._as_str(deployment_data.get("status"))
        environment = self._as_str(deployment_data.get("environment")) or self._as_str(
            data.get("environment")
        )
        url = self._as_str(data.get("url"))

        metadata: dict[str, str] = {}
        region = self._as_str(deployment_data.get("region"))
        if region:
            metadata["region"] = region
        service_id = self._as_str(data.get("serviceId"))
        if service_id:
            metadata["service_id"] = service_id
        project_id = self._as_str(data.get("projectId"))
        if project_id:
            metadata["project_id"] = project_id

        health = (
            "healthy" if deployment_status and deployment_status.lower() == "success" else "unknown"
        )

        return ServiceStatus(
            provider=self.name,
            project=project_name,
            service=service_name,
            deployment_id=deployment_id,
            deployment_status=deployment_status,
            environment=environment,
            url=url,
            health=health,
            metadata=metadata,
        )

    def logs(self, scope: RemoteServiceScope, *, lines: int, follow: bool) -> None:
        args = ["logs", "--tail", str(lines)]
        if follow:
            args.append("--follow")
        self._railway_command(args, scope=scope, capture_output=False)

    def fetch_logs(self, scope: RemoteServiceScope, *, lines: int) -> str:
        result = self._railway_command(
            ["logs", "--tail", str(lines)],
            scope=scope,
            capture_output=True,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if stderr and not stdout:
            return stderr
        if stderr:
            return f"{stdout}\n[stderr: {stderr}]"
        return stdout

    def restart(self, scope: RemoteServiceScope) -> RestartResult:
        data = self._read_json(["redeploy", "--json"], scope=scope)
        deployment_id = self._as_str(data.get("id"))
        status = self._as_str(data.get("status")) or "queued"
        return RestartResult(
            provider=self.name,
            project=scope.project,
            service=scope.service,
            requested=True,
            deployment_id=deployment_id,
            message=f"Railway redeploy requested ({status}).",
        )


def resolve_remote_ops_provider(provider: str) -> RemoteOpsProvider:
    """Resolve a provider-specific remote ops implementation."""
    normalized = provider.strip().lower()
    if normalized == "railway":
        return RailwayRemoteOpsProvider()
    raise RemoteOpsError(f"Unsupported remote ops provider: {provider}")
