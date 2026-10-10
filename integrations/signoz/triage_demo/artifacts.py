"""Checksum-verified upstream artifacts and bounded argument-vector execution."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import tarfile
from pathlib import Path

import httpx

from config.constants.triage_demo import (
    DEMO_FOUNDRY_CHECKSUMS,
    DEMO_FOUNDRY_VERSION,
    DEMO_OTEL_ARCHIVE_SHA256,
    DEMO_OTEL_COMMIT,
)


def run(argv: list[str], cwd: Path, *, timeout: float = 900) -> str:
    """Run no shell, suppress credential-bearing provider/Compose errors."""
    try:
        process = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"{argv[0]} could not complete ({type(exc).__name__})") from None
    if process.returncode:
        raise RuntimeError(
            f"{argv[0]} failed (exit {process.returncode}); inspect the owned demo resources"
        )
    return process.stdout


def download(url: str, destination: Path, digest: str) -> None:
    """Verify before extracting or executing; download only fixed release URLs."""
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
        return
    temporary = destination.with_suffix(".download")
    hasher = hashlib.sha256()
    size = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for block in response.iter_bytes():
                size += len(block)
                if size > 200 * 1024**2:
                    raise ValueError("Release download exceeds the demo size budget")
                hasher.update(block)
                stream.write(block)
    if hasher.hexdigest() != digest:
        temporary.unlink(missing_ok=True)
        raise ValueError("Upstream artifact checksum mismatch; nothing executed")
    temporary.replace(destination)


def extract(archive: Path, destination: Path) -> None:
    """Extract regular files/directories only with bounded expanded size."""
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if (
            any(not (m.isfile() or m.isdir()) for m in members)
            or sum(m.size for m in members) > 600 * 1024**2
        ):
            raise ValueError("Release archive contains links/devices or exceeds extraction budget")
        source.extractall(destination, members=members, filter="data")


def prepare(root: Path) -> tuple[Path, Path]:
    """Fetch pinned Foundry and immutable OpenTelemetry source into this demo."""
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    target = f"{platform.system().lower()}_{arch}"
    if target not in DEMO_FOUNDRY_CHECKSUMS:
        raise ValueError("Demo supports Linux, macOS, or Linux in WSL, on amd64/arm64")
    archive = root / "foundry.tar.gz"
    binary = root / f"foundry_{target}" / "bin" / "foundryctl"
    download(
        f"https://github.com/SigNoz/signoz-foundry/releases/download/{DEMO_FOUNDRY_VERSION}/foundry_{target}.tar.gz",
        archive,
        DEMO_FOUNDRY_CHECKSUMS[target],
    )
    # Re-extraction ensures a changed cached executable never bypasses verification.
    extract(archive, root)
    binary.chmod(0o700)
    archive = root / "otel.tar.gz"
    download(
        f"https://codeload.github.com/open-telemetry/opentelemetry-demo/tar.gz/{DEMO_OTEL_COMMIT}",
        archive,
        DEMO_OTEL_ARCHIVE_SHA256,
    )
    application = root / f"opentelemetry-demo-{DEMO_OTEL_COMMIT}"
    if not application.exists():
        extract(archive, root)
    return binary, application
