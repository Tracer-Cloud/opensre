"""Ollama server and model lifecycle management."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx

from config.llm_models import DEFAULT_OLLAMA_HOST
from infrastructure.terminal.theme import DIM, WARNING
from surfaces.shared.llm_setup.ollama import (
    OllamaModelDiscoveryError,
    list_available_models,
    model_is_available,
)
from surfaces.shared.llm_setup.ollama import (
    normalize_model_tag as normalize_model_tag,
)

if TYPE_CHECKING:
    from rich.console import Console


def is_installed() -> bool:
    return shutil.which("ollama") is not None


def install(console: Console) -> bool:
    """Show the install command, confirm with user, execute. Returns True on success."""
    import questionary

    if sys.platform == "darwin":
        if shutil.which("brew"):
            cmd = "brew install ollama"
            console.print(f"Will run: [bold]{cmd}[/bold]")
            if not questionary.confirm("Proceed?", default=True).ask():
                return False
            result = subprocess.run(["brew", "install", "ollama"], check=False)
            return result.returncode == 0
        console.print(f"[{WARNING}]Homebrew not found.[/]")
        console.print("Install Ollama from: [link]https://ollama.com/download/mac[/link]")
        return False

    elif sys.platform == "linux":
        cmd = "curl -fsSL https://ollama.com/install.sh | sh"
        console.print(f"Will run: [bold]{cmd}[/bold]")
        if not questionary.confirm("Proceed?", default=True).ask():
            return False
        result = subprocess.run(cmd, shell=True, check=False)
        return result.returncode == 0

    elif sys.platform == "win32":
        console.print(f"[{WARNING}]Windows is not yet supported by this automated setup.[/]")
    console.print("Install Ollama from: [link]https://ollama.com/download[/link]")
    return False


def is_server_running(host: str = DEFAULT_OLLAMA_HOST) -> bool:
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        return r.status_code == HTTPStatus.OK
    except Exception:
        return False


def start_server() -> subprocess.Popen:  # type: ignore[type-arg]
    return subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_server(host: str, timeout_s: int = 30) -> bool:
    for _ in range(timeout_s):
        if is_server_running(host):
            return True
        time.sleep(1)
    return False


def is_model_present(model: str, host: str = DEFAULT_OLLAMA_HOST) -> bool:
    """Return True if the model tag is already pulled."""
    try:
        available_models = list_available_models(host)
    except OllamaModelDiscoveryError:
        return False
    return model_is_available(model, available_models)


def pull_model(model: str, console: Console, host: str = DEFAULT_OLLAMA_HOST) -> bool:
    """Pull a model from the Ollama registry. Skips if already present. Returns True on success."""
    if is_model_present(model, host):
        console.print(f"[{DIM}]Model '{model}' already present, skipping download.[/]")
        return True
    with console.status(
        f"Downloading [bold]{model}[/bold] (this may take a few minutes)...", spinner="dots"
    ):
        result = subprocess.run(["ollama", "pull", model], check=False)
    return result.returncode == 0
