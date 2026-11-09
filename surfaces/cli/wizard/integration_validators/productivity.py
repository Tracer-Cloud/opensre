"""Client-backed validators for productivity/ticketing integrations."""

from __future__ import annotations

from pathlib import Path

from integrations.config_models import GoogleDocsIntegrationConfig

from .shared import IntegrationHealthResult


def validate_google_docs_integration(
    *,
    credentials_file: str,
    folder_id: str,
) -> IntegrationHealthResult:
    """Validate Google Docs credentials and folder access."""
    from integrations.google_docs import GoogleDocsClient

    try:
        config = GoogleDocsIntegrationConfig.model_validate(
            {
                "credentials_file": credentials_file,
                "folder_id": folder_id,
            }
        )
    except Exception as err:
        return IntegrationHealthResult(ok=False, detail=str(err))

    if not config.credentials_file or not config.folder_id:
        return IntegrationHealthResult(ok=False, detail="Missing credentials_file or folder_id.")

    if not Path(config.credentials_file).exists():
        return IntegrationHealthResult(
            ok=False, detail=f"Credentials file not found: {config.credentials_file}"
        )

    try:
        client = GoogleDocsClient(config)
        result = client.validate_access()
    except Exception as exc:
        return IntegrationHealthResult(ok=False, detail=f"Google API validation failed: {exc}")

    if not result.get("success"):
        return IntegrationHealthResult(
            ok=False, detail=f"Folder access check failed: {result.get('error', 'unknown error')}"
        )

    return IntegrationHealthResult(
        ok=True,
        detail=f"Connected to Drive folder {config.folder_id} ({result.get('file_count', 0)} items).",
    )
