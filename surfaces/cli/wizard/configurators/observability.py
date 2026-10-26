"""Configurator handlers for log/metric/trace backends."""

from __future__ import annotations

from config.env_file import sync_env_values
from infrastructure.terminal.theme import ERROR, HIGHLIGHT, SECONDARY
from integrations.coralogix import CORALOGIX_SETUP
from integrations.datadog import DATADOG_SETUP
from integrations.grafana import GRAFANA_SETUP
from integrations.honeycomb import HONEYCOMB_SETUP
from integrations.new_relic import NEW_RELIC_SETUP
from integrations.opensearch import OPENSEARCH_SETUP
from integrations.store import remove_integration, upsert_integration
from integrations.tempo.setup import TEMPO_SETUP
from surfaces.cli.wizard.components import (
    confirm,
    console,
    integration_defaults,
    prompt_value,
    string_value,
)
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec
from surfaces.cli.wizard.integration_health import (
    validate_splunk_integration,
)
from surfaces.cli.wizard.summaries import render_integration_result


def _configure_grafana() -> tuple[str, str]:
    return configure_from_spec(GRAFANA_SETUP, title="Grafana")


def _configure_grafana_local() -> tuple[str, str]:
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("docker"):
        console.print(f"[{ERROR}]Docker not found.[/]")
        console.print(f"[{SECONDARY}]Install Docker Desktop and retry.[/]")
        return "Grafana Local (skipped)", ""

    # Check Docker daemon is actually running
    ping = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if ping.returncode != 0:
        console.print(f"[{ERROR}]Docker is not running.[/]")
        console.print(
            f"[{SECONDARY}]Start Docker Desktop, then run "
            "[bold]opensre integrations setup grafana[/bold] again.[/]"
        )
        return "Grafana Local (skipped)", ""

    compose_file = str(Path(__file__).parent.parent / "local_grafana_stack/docker-compose.yml")
    with console.status("Starting Grafana + Loki (docker compose up -d)...", spinner="dots"):
        result = subprocess.run(
            ["docker", "compose", "-f", compose_file, "up", "-d"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if result.returncode != 0:
        console.print(f"[{ERROR}]Docker compose failed.[/]")
        console.print(result.stderr or result.stdout)
        return "Grafana Local (skipped)", ""

    with console.status("Waiting for Loki to be ready and seeding logs...", spinner="dots"):
        try:
            from surfaces.cli.wizard.grafana_seed import seed_logs

            seed_logs()
        except (SystemExit, Exception) as exc:
            console.print(f"[{ERROR}]Loki seed failed: {exc}[/]")
            return "Grafana Local (skipped)", ""

    endpoint = "http://localhost:3000"
    api_key = ""
    remove_integration("grafana")  # clean up any stale grafana record pointing to localhost
    upsert_integration("grafana_local", {"credentials": {"endpoint": endpoint, "api_key": api_key}})
    env_path = sync_env_values({"GRAFANA_INSTANCE_URL": endpoint})
    console.print(f"[{HIGHLIGHT}]Grafana Local · ready[/]")
    console.print(f"[{SECONDARY}]UI: {endpoint}[/]")
    console.print(f"[{SECONDARY}]Loki seeded with events_fact pipeline failure logs.[/]")
    return "Grafana Local", str(env_path)


def _configure_datadog() -> tuple[str, str]:
    return configure_from_spec(DATADOG_SETUP, title="Datadog")


def _configure_honeycomb() -> tuple[str, str]:
    return configure_from_spec(HONEYCOMB_SETUP, title="Honeycomb")


def _configure_coralogix() -> tuple[str, str]:
    return configure_from_spec(CORALOGIX_SETUP, title="Coralogix")


def _configure_new_relic() -> tuple[str, str]:
    return configure_from_spec(NEW_RELIC_SETUP, title="New Relic")


_TEMPO_INTRO = (
    f"[{SECONDARY}]Tempo commonly runs without auth behind a gateway — a URL alone is enough.\n"
    "For auth, provide either a bearer token OR a username/password (not both).[/]"
)


def _configure_tempo() -> tuple[str, str]:
    return configure_from_spec(TEMPO_SETUP, title="Tempo", intro=_TEMPO_INTRO)


def _configure_splunk() -> tuple[str, str]:
    _, credentials = integration_defaults("splunk")
    while True:
        base_url = prompt_value(
            "Splunk REST API base URL (e.g. https://splunk.corp.com:8089)",
            default=string_value(credentials.get("base_url")),
        )
        token = prompt_value(
            "Splunk API bearer token",
            default=string_value(credentials.get("token")),
            secret=True,
        )
        index = prompt_value(
            "Default Splunk index to search",
            default=string_value(credentials.get("index"), "main"),
        )
        verify_ssl = confirm(
            "Verify SSL certificate?",
            default=bool(credentials.get("verify_ssl", True)),
        )
        ca_bundle = ""
        if verify_ssl:
            ca_bundle = prompt_value(
                "Path to CA bundle for SSL verification (leave empty to use system defaults)",
                default=string_value(credentials.get("ca_bundle")),
                allow_empty=True,
            )
        with console.status("Validating Splunk integration...", spinner="dots"):
            result = validate_splunk_integration(
                base_url=base_url,
                token=token,
                index=index,
                verify_ssl=verify_ssl,
                ca_bundle=ca_bundle,
            )
        render_integration_result("Splunk", result)
        if result.ok:
            upsert_integration(
                "splunk",
                {
                    "credentials": {
                        "base_url": base_url,
                        "token": token,
                        "index": index,
                        "verify_ssl": verify_ssl,
                        "ca_bundle": ca_bundle,
                    }
                },
            )
            env_values: dict[str, str] = {
                "SPLUNK_URL": base_url,
                "SPLUNK_INDEX": index,
                "SPLUNK_VERIFY_SSL": "true" if verify_ssl else "false",
                # Do NOT write SPLUNK_TOKEN to .env — it goes to the credential store only
            }
            if ca_bundle:
                env_values["SPLUNK_CA_BUNDLE"] = ca_bundle
            env_path = sync_env_values(env_values)
            return "Splunk", str(env_path)
        console.print(f"[{SECONDARY}]Try again or press Ctrl+C to cancel.[/]")


def _configure_opensearch() -> tuple[str, str]:
    return configure_from_spec(OPENSEARCH_SETUP, title="OpenSearch")
