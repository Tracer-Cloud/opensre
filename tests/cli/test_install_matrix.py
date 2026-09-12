"""Install-process matrix: every path we tell users (or contributors) to use.

Covers docs contracts + sandboxed ``install.sh`` end-to-end runs (curl shim,
no live GitHub / no machine-wide PATH writes). PowerShell and Homebrew are
source/docs contracts here — full Windows/brew mutation stays out of CI.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as py_platform
import shutil
import stat
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

from config.constants.paths import REPO_ROOT

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "install.sh end-to-end matrix is POSIX-only; Windows uses install.ps1 "
        "(covered by test_install_ps1_progress + source contracts below)."
    ),
)

INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
DOCKERFILE = REPO_ROOT / "Dockerfile"
MAKEFILE = REPO_ROOT / "Makefile"
README = REPO_ROOT / "README.md"
QUICKSTART = REPO_ROOT / "docs" / "quickstart.mdx"
INSTALL_MDX = REPO_ROOT / "docs" / "install.mdx"
INSTALL_LOCAL = REPO_ROOT / "docs" / "install-local.mdx"
SETUP = REPO_ROOT / "SETUP.md"
HOMEBREW_SYNC = REPO_ROOT / ".github" / "scripts" / "sync-homebrew-tap-formula.sh"


#: Release-asset naming for the hosts install.sh publishes builds for.
_HOST_PLATFORMS = {"darwin": "darwin", "linux": "linux"}
_HOST_ARCHES = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}


def _host_platform_arch() -> tuple[str, str]:
    """Release-asset ``(platform, arch)`` for this host, or skip if unsupported."""
    system = py_platform.system().lower()
    plat = _HOST_PLATFORMS.get(system)
    if plat is None:
        pytest.skip(f"unsupported host for install.sh e2e: {system}")
    machine = py_platform.machine().lower()
    arch = _HOST_ARCHES.get(machine)
    if arch is None:
        pytest.skip(f"unsupported arch for install.sh e2e: {machine}")
    return plat, arch


def _write_fake_opensre(binary: Path, *, version_line: str) -> None:
    binary.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            if [ "${{1:-}}" = "--version" ]; then
              printf '%s\\n' {json.dumps(version_line)}
              exit 0
            fi
            printf 'opensre-stub\\n'
            exit 0
            """
        ),
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build_release_assets(
    assets_dir: Path,
    *,
    archive_version: str,
    plat: str,
    arch: str,
    binary_version_line: str,
) -> str:
    """Build ``opensre_<ver>_<plat>-<arch>.tar.gz`` + ``.sha256``; return archive name."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"opensre_{archive_version}_{plat}-{arch}.tar.gz"
    staging = assets_dir / f"staging-{archive_version}-{plat}-{arch}"
    staging.mkdir(parents=True, exist_ok=True)
    _write_fake_opensre(staging / "opensre", version_line=binary_version_line)
    archive_path = assets_dir / archive_name
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(staging / "opensre", arcname="opensre")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (assets_dir / f"{archive_name}.sha256").write_text(
        f"{digest}  {archive_name}\n",
        encoding="utf-8",
    )
    return archive_name


def _write_curl_shim(bin_dir: Path, assets_dir: Path, release_json_by_url: dict[str, str]) -> None:
    """Shim ``curl`` so install.sh never hits the network."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = bin_dir / "url_map.json"
    mapping_path.write_text(json.dumps(release_json_by_url), encoding="utf-8")
    shim = bin_dir / "curl"
    shim.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -euo pipefail
            out=""
            url=""
            args=("$@")
            i=0
            while [ "$i" -lt "${{#args[@]}}" ]; do
              arg="${{args[$i]}}"
              case "$arg" in
                -o|--output)
                  i=$((i + 1))
                  out="${{args[$i]}}"
                  ;;
                -H|--header|--retry|--retry-delay) i=$((i + 1)) ;;
                --fail|--silent|--show-error|--location) ;;
                http://*|https://*) url="$arg" ;;
              esac
              i=$((i + 1))
            done
            [ -n "$url" ] || {{ echo "curl-shim: missing url: $*" >&2; exit 2; }}
            map={json.dumps(str(mapping_path))}
            assets={json.dumps(str(assets_dir))}
            if printf '%s' "$url" | grep -q 'api.github.com'; then
              body="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$map" "$url")"
              if [ -n "$out" ]; then printf '%s' "$body" >"$out"; else printf '%s' "$body"; fi
              exit 0
            fi
            if printf '%s' "$url" | grep -q 'releases/download/'; then
              name="$(basename "$url")"
              src="$assets/$name"
              [ -f "$src" ] || {{ echo "curl-shim: missing asset $src for $url" >&2; exit 1; }}
              if [ -n "$out" ]; then cp "$src" "$out"; else cat "$src"; fi
              exit 0
            fi
            echo "curl-shim: unhandled url $url" >&2
            exit 1
            """
        ),
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_install_sh(
    tmp_path: Path,
    *args: str,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    plat, arch = _host_platform_arch()
    home = tmp_path / "home"
    home.mkdir()
    install_dir = tmp_path / "opt" / "bin"
    install_dir.mkdir(parents=True)
    assets = tmp_path / "assets"
    shim_bin = tmp_path / "shim-bin"

    # Main-channel asset (default / --main)
    main_archive = _build_release_assets(
        assets,
        archive_version="main",
        plat=plat,
        arch=arch,
        binary_version_line="opensre 0.1 (dev, main @ deadbeef)",
    )
    # Versioned release asset
    version = "2026.4.29"
    release_archive = _build_release_assets(
        assets,
        archive_version=version,
        plat=plat,
        arch=arch,
        binary_version_line=f"opensre {version}",
    )

    main_json = json.dumps(
        {
            "tag_name": "main-build",
            "assets": [{"name": main_archive}, {"name": f"{main_archive}.sha256"}],
        }
    )
    version_json = json.dumps(
        {
            "tag_name": f"v{version}",
            "assets": [
                {"name": release_archive},
                {"name": f"{release_archive}.sha256"},
            ],
        }
    )
    latest_json = version_json
    url_map = {
        "https://api.github.com/repos/Tracer-Cloud/opensre/releases/tags/main-build": main_json,
        f"https://api.github.com/repos/Tracer-Cloud/opensre/releases/tags/v{version}": version_json,
        "https://api.github.com/repos/Tracer-Cloud/opensre/releases/latest": latest_json,
    }
    _write_curl_shim(shim_bin, assets, url_map)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{shim_bin}{os.pathsep}{env.get('PATH', '')}"
    env["OPENSRE_AUTO_LAUNCH"] = "0"
    env["OPENSRE_SKIP_GH_INSTALL"] = "1"
    env["OPENSRE_INSTALL_VERBOSE"] = "1"
    env["TERM"] = "dumb"
    if env_extra:
        env.update(env_extra)

    cmd = ["bash", str(INSTALL_SH), "--install-dir", str(install_dir), *args]
    return subprocess.run(
        cmd,
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


# ── Docs / surface contracts ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "needles"),
    [
        (
            README,
            (
                "curl -fsSL https://install.opensre.com | bash",
                "brew tap tracer-cloud/tap",
                "brew install tracer-cloud/tap/opensre",
                "irm https://install.opensre.com | iex",
            ),
        ),
        (
            QUICKSTART,
            (
                "brew tap tracer-cloud/tap",
                "brew install tracer-cloud/tap/opensre",
                "curl -fsSL https://install.opensre.com | bash",
                "irm https://install.opensre.com | iex",
            ),
        ),
        (
            INSTALL_MDX,
            (
                "curl -fsSL https://install.opensre.com | bash",
                "irm https://install.opensre.com | iex",
                "opensre onboard",
            ),
        ),
        (
            INSTALL_LOCAL,
            (
                "brew tap tracer-cloud/tap",
                "brew install tracer-cloud/tap/opensre",
                "curl -fsSL https://install.opensre.com | bash",
                "OPENSRE_AUTO_LAUNCH=0",
                "OPENSRE_SKIP_GH_INSTALL=1",
                "Homebrew installs pull in `gh` automatically.",
            ),
        ),
        (
            SETUP,
            (
                "make install",
                "uv sync --frozen --extra dev",
            ),
        ),
    ],
    ids=["readme", "quickstart", "install", "install-local", "setup"],
)
def test_install_docs_list_every_process(path: Path, needles: tuple[str, ...]) -> None:
    text = path.read_text(encoding="utf-8")
    for needle in needles:
        assert needle in text, f"{path.name} missing install step {needle!r}"


def test_install_sh_help_lists_all_channels() -> None:
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for needle in (
        "--main",
        "--release",
        "--version",
        "--install-dir",
        "curl -fsSL https://install.opensre.com | bash",
        "bash -s -- --main",
        "bash -s -- --version",
    ):
        assert needle in out, f"install.sh --help missing {needle!r}"


def test_install_sh_source_exposes_env_knobs() -> None:
    source = INSTALL_SH.read_text(encoding="utf-8")
    for needle in (
        "OPENSRE_SKIP_GH_INSTALL",
        "OPENSRE_INSTALL_CHANNEL",
        "OPENSRE_INSTALL_DIR",
        "OPENSRE_VERSION",
        "OPENSRE_MAIN_RELEASE_TAG",
        "OPENSRE_INSTALL_VERBOSE",
        "OPENSRE_INSTALL_REPO",
        'INSTALL_CHANNEL="${OPENSRE_INSTALL_CHANNEL:-main}"',
        "ensure_github_cli",
        "warm_first_launch",
        "package_smoke_quiet",
        "_package-smoke",
    ):
        assert needle in source, f"install.sh missing {needle!r}"


def test_install_ps1_source_exposes_all_windows_install_knobs() -> None:
    source = INSTALL_PS1.read_text(encoding="utf-8")
    for needle in (
        "param(",
        "$Channel",
        "$InstallDir",
        "OPENSRE_INSTALL_CHANNEL",
        "OPENSRE_AUTO_LAUNCH",
        "OPENSRE_SKIP_GH_INSTALL",
        "OPENSRE_VERSION",
        "OPENSRE_MAIN_RELEASE_TAG",
        "OPENSRE_INSTALL_VERBOSE",
        "winget install --id GitHub.cli",
        "Start-OpenSreOnboardingAfterInstall",
        'else { "main" }',
        "main-build",
        "$exe setup",
        "Invoke-OpenSreFirstLaunchWarmup",
        "_package-smoke",
    ):
        assert needle in source, f"install.ps1 missing {needle!r}"


def test_homebrew_sync_script_updates_formula_checksums() -> None:
    source = HOMEBREW_SYNC.read_text(encoding="utf-8")
    assert "Formula/opensre.rb" in source
    assert "darwin-arm64" in source
    assert "linux-x64" in source
    assert 'version "' in source or "version =" in source
    assert "DRY_RUN" in source


def test_dockerfile_installs_runtime_entrypoint() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM python:" in text
    assert "opensre gateway" in text or "uvicorn gateway.web.webapp" in text


def test_makefile_install_uses_uv_sync() -> None:
    text = MAKEFILE.read_text(encoding="utf-8")
    assert "install:" in text
    assert "uv sync --frozen --extra dev" in text


# ── Sandboxed install.sh end-to-end (all channels) ───────────────────────────


def test_install_sh_main_channel_end_to_end(tmp_path: Path) -> None:
    result = _run_install_sh(tmp_path, "--main")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    installed = tmp_path / "opt" / "bin" / "opensre"
    assert installed.is_file()
    assert os.access(installed, os.X_OK)
    version = subprocess.run(
        [str(installed), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert version.returncode == 0
    assert "opensre" in version.stdout.lower() or "0.1" in version.stdout
    assert "Welcome to OpenSRE" in combined or "installed successfully" in combined
    assert "opensre onboard" not in combined


def test_install_sh_version_channel_end_to_end(tmp_path: Path) -> None:
    result = _run_install_sh(tmp_path, "--version", "2026.4.29")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    installed = tmp_path / "opt" / "bin" / "opensre"
    assert installed.is_file()
    version = subprocess.run(
        [str(installed), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "2026.4.29" in version.stdout


def test_install_sh_release_latest_end_to_end(tmp_path: Path) -> None:
    result = _run_install_sh(tmp_path, "--release")
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    installed = tmp_path / "opt" / "bin" / "opensre"
    assert installed.is_file()
    version = subprocess.run(
        [str(installed), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "2026.4.29" in version.stdout


def test_install_sh_rejects_version_with_main(tmp_path: Path) -> None:
    result = _run_install_sh(tmp_path, "--main", "--version", "2026.4.29")
    assert result.returncode != 0
    assert "cannot be combined" in (result.stdout + result.stderr)


def test_install_sh_unknown_flag_fails(tmp_path: Path) -> None:
    result = _run_install_sh(tmp_path, "--not-a-real-flag")
    assert result.returncode != 0
    assert "Unknown argument" in (result.stdout + result.stderr)


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="glibc preflight only runs when install.sh detects platform=linux",
)
def test_install_sh_rejects_incompatible_glibc(tmp_path: Path) -> None:
    """An old-glibc host fails fast with an actionable message, before any download.

    Regression for https://github.com/Tracer-Cloud/opensre/issues/5993: the
    release binary is built (and glibc-symbol-capped) on ubuntu-22.04, so it
    cannot start on Ubuntu 20.04 (glibc 2.31). Without this preflight, users
    only saw a cryptic PyInstaller loader error after downloading the archive.
    """
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    getconf_shim = fake_bin / "getconf"
    getconf_shim.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            if [ "${1:-}" = "GNU_LIBC_VERSION" ]; then
              printf 'glibc 2.31\\n'
              exit 0
            fi
            exit 1
            """
        ),
        encoding="utf-8",
    )
    getconf_shim.chmod(getconf_shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--main"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "glibc 2.35" in combined
    assert "glibc 2.31" in combined
    assert "uv run opensre" in combined


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="glibc preflight only runs when install.sh detects platform=linux",
)
def test_install_sh_falls_back_to_ldd_when_getconf_cannot_report_version(tmp_path: Path) -> None:
    """A present-but-uninformative getconf (no GNU_LIBC_VERSION) must not skip ldd.

    Regression for a review finding on the fix for #5993: the preferred
    (getconf) detection branch used to return unconditionally, so a host
    where getconf exists but can't report a glibc version silently bypassed
    the check instead of falling back to `ldd --version`.
    """
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    (fake_bin / "getconf").write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getconf").chmod(
        (fake_bin / "getconf").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    (fake_bin / "ldd").write_text(
        "#!/usr/bin/env bash\nprintf 'ldd (Ubuntu GLIBC 2.31-0ubuntu1) 2.31\\n'\n",
        encoding="utf-8",
    )
    (fake_bin / "ldd").chmod(
        (fake_bin / "ldd").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--main"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "glibc 2.31" in combined


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="libc preflight only runs when install.sh detects platform=linux",
)
def test_install_sh_rejects_musl_before_download(tmp_path: Path) -> None:
    """A known non-glibc host must fail before fetching an incompatible binary."""
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    network_marker = tmp_path / "network-reached"

    (fake_bin / "getconf").write_text(
        "#!/usr/bin/env bash\nexit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "getconf").chmod(
        (fake_bin / "getconf").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    (fake_bin / "ldd").write_text(
        "#!/usr/bin/env bash\nprintf 'musl libc (x86_64)\\nVersion 1.2.5\\n' >&2\nexit 1\n",
        encoding="utf-8",
    )
    (fake_bin / "ldd").chmod(
        (fake_bin / "ldd").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )
    (fake_bin / "curl").write_text(
        '#!/usr/bin/env bash\nprintf reached > "$NETWORK_MARKER"\nexit 1\n',
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(
        (fake_bin / "curl").stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["NETWORK_MARKER"] = str(network_marker)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--main"],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "musl libc" in combined
    assert "glibc 2.35" in combined
    assert "uv run opensre" in combined
    assert not network_marker.exists()


def test_install_sh_skip_gh_and_no_auto_launch_env(tmp_path: Path) -> None:
    """Documented CI/provisioning knobs must keep install non-interactive."""
    result = _run_install_sh(
        tmp_path,
        "--main",
        env_extra={
            "OPENSRE_AUTO_LAUNCH": "0",
            "OPENSRE_SKIP_GH_INSTALL": "1",
        },
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "Launching" not in combined
    # With gh missing and SKIP set, installer warns rather than failing.
    assert "OPENSRE_SKIP_GH_INSTALL" in combined or (tmp_path / "opt" / "bin" / "opensre").is_file()


@pytest.mark.skipif(shutil.which("brew") is None, reason="Homebrew not installed on this host")
def test_homebrew_formula_resolvable_when_brew_present() -> None:
    """Soft check: tap formula metadata is fetchable (no install/mutation)."""
    result = subprocess.run(
        ["brew", "info", "--json=v2", "tracer-cloud/tap/opensre"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"brew info unavailable: {result.stderr[-200:]}")
    payload = json.loads(result.stdout)
    formulae = payload.get("formulae") or []
    assert formulae, "brew info returned no formulae"
    name = formulae[0].get("name") or formulae[0].get("full_name")
    assert name and "opensre" in str(name)
