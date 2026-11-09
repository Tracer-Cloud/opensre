"""What Azure SQL needs before it is considered configured.

``encrypt`` used to be gathered via a CLI select menu; every other field was
always asked. Treating it as a defaulted text field keeps the same credentials
without a branching prompt feature.
"""

from __future__ import annotations

from config.constants.azure_sql import (
    AZURE_SQL_DATABASE_ENV,
    AZURE_SQL_DRIVER_ENV,
    AZURE_SQL_ENCRYPT_ENV,
    AZURE_SQL_PASSWORD_ENV,
    AZURE_SQL_PORT_ENV,
    AZURE_SQL_SERVER_ENV,
    AZURE_SQL_USERNAME_ENV,
    DEFAULT_AZURE_SQL_DRIVER,
    DEFAULT_AZURE_SQL_PORT,
)
from integrations.azure_sql.verifier import verify_azure_sql
from integrations.setup_flow import IntegrationSetupSpec, SetupField

SERVER_FIELD = "server"
DATABASE_FIELD = "database"
PORT_FIELD = "port"
USERNAME_FIELD = "username"
PASSWORD_FIELD = "password"
DRIVER_FIELD = "driver"
ENCRYPT_FIELD = "encrypt"

AZURE_SQL_SETUP = IntegrationSetupSpec(
    service="azure_sql",
    fields=(
        SetupField(
            name=SERVER_FIELD,
            label="Server",
            prompt="Server (e.g. myserver.database.windows.net)",
            env_var=AZURE_SQL_SERVER_ENV,
        ),
        SetupField(
            name=DATABASE_FIELD,
            label="Database name",
            env_var=AZURE_SQL_DATABASE_ENV,
        ),
        SetupField(
            name=PORT_FIELD,
            label="Port",
            env_var=AZURE_SQL_PORT_ENV,
            default=str(DEFAULT_AZURE_SQL_PORT),
        ),
        SetupField(
            name=USERNAME_FIELD,
            label="Username",
            env_var=AZURE_SQL_USERNAME_ENV,
            required=False,
        ),
        SetupField(
            name=PASSWORD_FIELD,
            label="Password",
            env_var=AZURE_SQL_PASSWORD_ENV,
            secret=True,
            required=False,
        ),
        SetupField(
            name=DRIVER_FIELD,
            label="ODBC driver",
            env_var=AZURE_SQL_DRIVER_ENV,
            default=DEFAULT_AZURE_SQL_DRIVER,
        ),
        SetupField(
            name=ENCRYPT_FIELD,
            label="Encrypt connection",
            prompt="Encrypt connection (true or false)",
            env_var=AZURE_SQL_ENCRYPT_ENV,
            default="true",
        ),
    ),
    verify=verify_azure_sql,
)

__all__ = [
    "AZURE_SQL_SETUP",
    "DATABASE_FIELD",
    "DRIVER_FIELD",
    "ENCRYPT_FIELD",
    "PASSWORD_FIELD",
    "PORT_FIELD",
    "SERVER_FIELD",
    "USERNAME_FIELD",
]
