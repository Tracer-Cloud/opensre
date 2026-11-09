"""Google Docs integration."""

from __future__ import annotations

from config.constants.google_docs import (
    GOOGLE_CREDENTIALS_FILE_ENV,
    GOOGLE_DRIVE_FOLDER_ID_ENV,
)
from integrations.google_docs.client import GoogleDocsClient
from integrations.google_docs.verifier import verify_google_docs
from integrations.setup_flow import IntegrationSetupSpec, SetupField

GOOGLE_DOCS_SETUP = IntegrationSetupSpec(
    service="google_docs",
    fields=(
        SetupField(
            name="credentials_file",
            label="Service account credentials JSON path",
            prompt="Path to Google service account credentials JSON file",
            env_var=GOOGLE_CREDENTIALS_FILE_ENV,
        ),
        SetupField(
            name="folder_id",
            label="Drive folder ID",
            prompt="Google Drive folder ID for incident reports",
            env_var=GOOGLE_DRIVE_FOLDER_ID_ENV,
        ),
    ),
    verify=verify_google_docs,
)

__all__ = ["GOOGLE_DOCS_SETUP", "GoogleDocsClient"]
