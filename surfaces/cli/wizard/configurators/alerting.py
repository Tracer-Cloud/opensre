"""Configurator handlers for alerting and on-call integrations."""

from __future__ import annotations

from integrations.alertmanager import ALERTMANAGER_SETUP
from integrations.betterstack.setup import BETTERSTACK_SETUP
from integrations.incident_io import INCIDENT_IO_SETUP
from integrations.pagerduty import PAGERDUTY_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_betterstack() -> tuple[str, str]:
    return configure_from_spec(BETTERSTACK_SETUP, title="Better Stack")


def _configure_alertmanager() -> tuple[str, str]:
    return configure_from_spec(ALERTMANAGER_SETUP, title="Alertmanager")


def _configure_pagerduty() -> tuple[str, str]:
    return configure_from_spec(PAGERDUTY_SETUP, title="PagerDuty")


def _configure_incident_io() -> tuple[str, str]:
    return configure_from_spec(INCIDENT_IO_SETUP, title="incident.io")
