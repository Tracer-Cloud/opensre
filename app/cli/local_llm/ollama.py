"""Ollama server and model lifecycle management."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from subprocess import DEVNULL
from typing import TYPE_CHECKING

import httpx

from app.config import DEFAULT_OLLAMA_HOST

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
        console.print("[yellow]Homebrew not found.[/yellow]")
        console.print("Install Ollama from: [link]https://ollama.com/download/mac[/link]")
        return False

    elif sys.platform == "linux":
        cmd = "curl -fsSL https://ollama.com/install.sh | sh"
        console.print(f"Will run: [bold]{cmd}[/bold]")
        if not questionary.confirm("Proceed?", default=True).ask():
            return False
        result = subprocess.run(cmd, shell=True, check=False)  # noqa: S602
        return result.returncode == 0

    elif sys.platform == "win32":
        console.print("[yellow]Windows is not yet supported by this automated setup.[/yellow]")
    console.print("Install Ollama from: [link]https://ollama.com/download[/link]")
    return False


def is_server_running(host: str = DEFAULT_OLLAMA_HOST) -> bool:
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


def start_server() -> subprocess.Popen:  # type: ignore[type-arg]
    return subprocess.Popen(["ollama", "serve"], stdout=DEVNULL, stderr=DEVNULL)  # noqa: S603, S607


def wait_for_server(host: str, timeout_s: int = 30) -> bool:
    for _ in range(timeout_s):
        if is_server_running(host):
            return True
        time.sleep(1)
    return False


def normalize_model_tag(model: str) -> str:
    """Ensure model has explicit tag. If no tag specified, append :latest to match Ollama behavior."""
    return model if ":" in model else f"{model}:latest"


def is_model_present(model: str, host: str = DEFAULT_OLLAMA_HOST) -> bool:
    """Return True if the model tag is already pulled."""
    try:
        r = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=5.0)
        r.raise_for_status()
        available = [m["name"] for m in r.json().get("models", [])]
        normalized_model = normalize_model_tag(model)
        return normalized_model in available
    except Exception:
        return False


def pull_model(model: str, console: Console, host: str = DEFAULT_OLLAMA_HOST) -> bool:
    """Pull a model from the Ollama registry. Skips if already present. Returns True on success."""
    if is_model_present(model, host):
        console.print(f"[dim]Model '{model}' already present, skipping download.[/dim]")
        return True
    with console.status(
        f"Downloading [bold]{model}[/bold] (this may take a few minutes)...", spinner="dots"
    ):
        result = subprocess.run(["ollama", "pull", model], check=False)  # noqa: S603, S607
    return result.returncode == 0
