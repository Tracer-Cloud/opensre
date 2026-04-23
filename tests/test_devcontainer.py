from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_devcontainer_config_matches_local_dev_workflow() -> None:
    config = json.loads((REPO_ROOT / ".devcontainer" / "devcontainer.json").read_text())
    dockerfile = (REPO_ROOT / ".devcontainer" / "Dockerfile").read_text()

    assert config["name"] == "OpenSRE"
    assert config["build"]["dockerfile"] == "Dockerfile"
    assert config["build"]["context"] == ".."
    assert (
        config["features"]["ghcr.io/devcontainers/features/docker-outside-of-docker:1"][
            "dockerDashComposeVersion"
        ]
        == "v2"
    )
    assert config["remoteEnv"]["LOCAL_WORKSPACE_FOLDER"] == "${localWorkspaceFolder}"
    assert (
        config["remoteEnv"]["PATH"]
        == "${containerWorkspaceFolder}/.venv-devcontainer/bin:${containerEnv:PATH}"
    )
    assert (
        config["customizations"]["vscode"]["settings"]["python.defaultInterpreterPath"]
        == "${containerWorkspaceFolder}/.venv-devcontainer/bin/python"
    )
    assert "python -m venv --clear .venv-devcontainer" in config["postCreateCommand"]
    assert "pip install --upgrade pip" not in config["postCreateCommand"]
    assert ".venv-devcontainer/bin/python -m pip install -e '.[dev]'" in config["postCreateCommand"]
    assert 2024 in config["forwardPorts"]
    assert "FROM python:3.13-bookworm" in dockerfile
    assert (
        "apt-get install -y --no-install-recommends ca-certificates curl git make sudo"
        in dockerfile
    )


def test_local_grafana_compose_uses_relative_provisioning_path() -> None:
    # Relative path resolves from the compose file's own directory, so it works
    # on the host (opensre onboard), via make grafana-local-up, and in devcontainer —
    # without requiring LOCAL_WORKSPACE_FOLDER to be set.
    compose = (REPO_ROOT / "app/cli/wizard/local_grafana_stack/docker-compose.yml").read_text()

    assert "./provisioning:/etc/grafana/provisioning" in compose
    assert "${LOCAL_WORKSPACE_FOLDER" not in compose
