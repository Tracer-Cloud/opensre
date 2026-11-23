"""A record written off this machine may name remote services, never local ones."""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.account import INTEGRATION_APP_ORIGIN, INTEGRATION_RETRIEVAL_ORIGIN_FIELD
from config.constants.billing import ORGANIZATION_ID_ENV
from integrations.catalog import classify_integrations

_PUBLIC_ADDRESS = "93.184.216.34"
_EXEC_KUBECONFIG = """
apiVersion: v1
clusters:
- name: c
  cluster: {server: 'https://93.184.216.34'}
users:
- name: u
  user:
    exec: {command: /bin/sh, args: ['-c', 'id']}
"""
_TOKEN_KUBECONFIG = """
apiVersion: v1
clusters:
- name: c
  cluster: {server: 'https://93.184.216.34'}
users:
- name: u
  user: {token: abc}
"""


def _record(service: str, credentials: dict[str, Any], *, from_app: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "1",
        "service": service,
        "status": "active",
        "name": "default",
        "credentials": credentials,
    }
    if from_app:
        record[INTEGRATION_RETRIEVAL_ORIGIN_FIELD] = INTEGRATION_APP_ORIGIN
    return record


def _services(record: dict[str, Any]) -> list[str]:
    return [key for key in classify_integrations([record]) if not key.startswith("_")]


@pytest.fixture(autouse=True)
def _unbound_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ORGANIZATION_ID_ENV, raising=False)


@pytest.fixture
def serving_an_organization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_1")


@pytest.mark.parametrize(
    ("service", "credentials"),
    [
        ("mcp", {"command": "/bin/sh", "args": "-c id"}),
        ("sentry_mcp", {"mode": "stdio", "command": "/usr/bin/whoami"}),
        ("posthog_mcp", {"mode": "stdio", "command": "/usr/bin/whoami"}),
        ("kubernetes", {"kubeconfig": _EXEC_KUBECONFIG}),
        ("kubernetes", {"kubeconfig_path": "/etc/passwd"}),
    ],
)
def test_a_record_from_the_app_cannot_run_a_program_or_read_a_file_here(
    service: str, credentials: dict[str, Any]
) -> None:
    assert _services(_record(service, credentials, from_app=True)) == []


@pytest.mark.parametrize(
    ("service", "credentials"),
    [
        ("mcp", {"command": "/bin/echo"}),
        ("kubernetes", {"kubeconfig": _EXEC_KUBECONFIG}),
    ],
)
def test_the_same_record_written_on_this_machine_still_works(
    service: str, credentials: dict[str, Any]
) -> None:
    assert _services(_record(service, credentials, from_app=False)) == [service]


@pytest.mark.usefixtures("serving_an_organization")
def test_every_record_on_a_host_serving_an_organization_is_remote() -> None:
    """The store there is a copy of the organization's own configuration."""
    assert _services(_record("mcp", {"command": "/bin/echo"}, from_app=False)) == []


@pytest.mark.parametrize(
    ("service", "credentials", "field"),
    [
        ("helm", {"helm_path": "/workspace/planted", "kubeconfig": "/tmp/k"}, "helm_path"),
        ("railway", {"token": "t", "railway_path": "/workspace/planted"}, "railway_path"),
    ],
)
def test_a_record_from_the_app_cannot_choose_the_executable(
    service: str, credentials: dict[str, Any], field: str
) -> None:
    config = classify_integrations([_record(service, credentials, from_app=True)])[service]

    assert getattr(config, field) == service


def test_remote_services_named_by_the_app_are_kept() -> None:
    assert _services(_record("mcp", {"url": f"https://{_PUBLIC_ADDRESS}/mcp"}, from_app=True)) == [
        "mcp"
    ]
    assert _services(_record("kubernetes", {"kubeconfig": _TOKEN_KUBECONFIG}, from_app=True)) == [
        "kubernetes"
    ]


@pytest.mark.usefixtures("serving_an_organization")
@pytest.mark.parametrize(
    ("service", "credentials"),
    [
        (
            "jenkins",
            {
                "base_url": "http://169.254.169.254/latest/meta-data/?",
                "username": "x",
                "api_token": "x",
            },
        ),
        ("jenkins", {"base_url": "http://localhost:8080", "username": "x", "api_token": "x"}),
        ("postgresql", {"host": "10.0.0.5", "database": "d", "username": "u", "password": "p"}),
        (
            "kubernetes",
            {"kubeconfig": _TOKEN_KUBECONFIG.replace(_PUBLIC_ADDRESS, "169.254.170.2")},
        ),
    ],
)
def test_an_organization_cannot_point_its_host_at_the_hosts_own_network(
    service: str, credentials: dict[str, Any]
) -> None:
    assert _services(_record(service, credentials, from_app=False)) == []


@pytest.mark.usefixtures("serving_an_organization")
def test_an_organization_can_point_its_host_at_public_addresses() -> None:
    jenkins = {"base_url": f"https://{_PUBLIC_ADDRESS}", "username": "x", "api_token": "x"}
    postgres = {"host": _PUBLIC_ADDRESS, "database": "d", "username": "u", "password": "p"}

    assert _services(_record("jenkins", jenkins, from_app=False)) == ["jenkins"]
    assert _services(_record("postgresql", postgres, from_app=False)) == ["postgresql"]


def test_a_laptop_may_reach_its_own_private_network_through_app_records() -> None:
    """Only the vendor's host has a network the organization must stay out of."""
    jenkins = {"base_url": "http://10.0.0.5:8080", "username": "x", "api_token": "x"}

    assert _services(_record("jenkins", jenkins, from_app=True)) == ["jenkins"]
