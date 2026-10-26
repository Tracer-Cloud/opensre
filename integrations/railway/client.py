from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from config.constants.railway import RAILWAY_TOKEN_ENV
from infrastructure.delivery.notifications.redaction import redact_token
from integrations.config_models import RailwayIntegrationConfig
from integrations.probes import ProbeResult
from integrations.railway.models import DeploymentInfo, RailwayScope

_TIMEOUT_SECONDS = 10
_ERROR_LIMIT = 500
_DEPLOYMENT_SEARCH_LIMIT = 100


class RailwayOperationError(RuntimeError):
    def __init__(
        self, message: str, *, error_type: str = "configuration_or_delivery_error"
    ) -> None:
        super().__init__(message)
        self.error_type = error_type


def railway_config_from_sources(sources: Mapping[str, object] | None) -> RailwayIntegrationConfig:
    source = (sources or {}).get("railway")
    if not isinstance(source, dict):
        return RailwayIntegrationConfig()
    raw_config = source.get("config")
    config = raw_config if isinstance(raw_config, dict) else source
    return RailwayIntegrationConfig.model_validate(config)


class RailwayClient:
    def __init__(self, config: RailwayIntegrationConfig) -> None:
        self._config = config

    def _railway_executable(self) -> str | None:
        configured = (self._config.railway_path or "railway").strip() or "railway"
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return str(candidate)
        candidates = (
            ("railway.cmd", "railway.exe", "railway")
            if os.name == "nt" and configured == "railway"
            else (configured,)
        )
        for executable in candidates:
            resolved = shutil.which(executable)
            if resolved:
                return resolved
        return None

    @property
    def is_available(self) -> bool:
        return self._railway_executable() is not None

    def _redact_error(self, text: str) -> str:
        redacted = redact_token(text, self._config.token)
        redacted = redact_token(redacted, os.getenv(RAILWAY_TOKEN_ENV, "").strip())
        return redacted[:_ERROR_LIMIT]

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        executable = self._railway_executable()
        if executable is None:
            raise RailwayOperationError(
                "Railway CLI is not installed. Install with: npm install -g @railway/cli"
            )
        environment = os.environ.copy()
        if self._config.token:
            environment["RAILWAY_TOKEN"] = self._config.token
        try:
            result = subprocess.run(
                [executable, *args],
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=_TIMEOUT_SECONDS,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RailwayOperationError("Railway CLI timed out.") from exc
        except OSError as exc:
            raise RailwayOperationError("Railway CLI could not be started.") from exc
        if result.returncode != 0:
            detail = self._redact_error(
                (result.stderr or result.stdout or "Railway CLI failed").strip()
            )
            raise RailwayOperationError(detail)
        return result

    def _json_output(self, result: subprocess.CompletedProcess[str]) -> object:
        try:
            return json.loads(result.stdout or "")
        except json.JSONDecodeError as exc:
            raise RailwayOperationError("Railway CLI returned invalid JSON.") from exc

    def resolve_scope(
        self, project: str = "", service: str = "", environment: str = ""
    ) -> tuple[str, str, str]:
        resolved = (
            project.strip() or self._config.project,
            service.strip() or self._config.service,
            environment.strip() or self._config.environment,
        )
        if not all(resolved):
            raise RailwayOperationError("Railway project, service, and environment are required.")
        for name, value in zip(("project", "service", "environment"), resolved, strict=True):
            if value.startswith("-"):
                raise RailwayOperationError(f"Invalid Railway {name}.")
        return resolved

    def resolve_scope_object(
        self, project: str = "", service: str = "", environment: str = ""
    ) -> RailwayScope:
        resolved_project, resolved_service, resolved_environment = self.resolve_scope(
            project, service, environment
        )
        return RailwayScope(
            project=resolved_project,
            service=resolved_service,
            environment=resolved_environment,
        )

    def probe_access(self) -> ProbeResult:
        if not self.is_available:
            return ProbeResult.missing(
                "Railway CLI is not installed. Install with: npm install -g @railway/cli"
            )
        try:
            self._run(["whoami", "--json"])
        except RailwayOperationError as exc:
            return ProbeResult.failed(str(exc))
        return ProbeResult.passed("Railway CLI is available and authenticated.")

    def inspect_deployment(self, scope: RailwayScope) -> DeploymentInfo:
        deployment = self._deployment_record(
            self._json_output(
                self._run(
                    [
                        "deployment",
                        "list",
                        "--project",
                        scope.project,
                        "--service",
                        scope.service,
                        "--environment",
                        scope.environment,
                        "--limit",
                        str(_DEPLOYMENT_SEARCH_LIMIT),
                        "--json",
                    ]
                )
            )
        )
        metadata = deployment.get("meta")
        meta = metadata if isinstance(metadata, dict) else {}
        deployment_id = str(deployment.get("id") or "").strip()
        if not deployment_id:
            raise RailwayOperationError("Railway deployment data did not include an ID.")
        return DeploymentInfo(
            deployment_id=deployment_id,
            status=str(deployment.get("status") or "").strip() or None,
            commit_hash=str(meta.get("commitHash") or "").strip() or None,
            commit_message=str(meta.get("commitMessage") or "").strip() or None,
        )

    def _deployment_record(self, data: object) -> dict[str, Any]:
        deployments = (
            data
            if isinstance(data, list)
            else data.get("deployments", [])
            if isinstance(data, dict)
            else []
        )
        successful = [
            deployment
            for deployment in deployments
            if isinstance(deployment, dict) and deployment.get("status") == "SUCCESS"
        ]
        if not successful:
            raise RailwayOperationError(
                "Railway returned no successful deployment data.",
                error_type="deployment_unavailable",
            )
        return max(successful, key=lambda deployment: str(deployment.get("createdAt") or ""))
