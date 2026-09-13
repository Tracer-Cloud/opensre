"""Contracts for deterministic code and data inputs in frozen releases."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from config.constants import (
    OPENSRE_INSTALL_REPLACE_EXISTING_BINARY_ENV,
    OPENSRE_UPDATE_PARENT_STARTED_ENV,
)
from infrastructure.deployment.packaging.release_manifest import (
    infrastructure_data_entries,
    required_skill_files,
    runtime_hidden_imports,
)
from infrastructure.deployment.packaging.windows_installer_constants import (
    render_installer_constants,
)
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES
from tools.registry_index import BAKED_INDEX_RELATIVE_PATH

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_SPEC_FILE = _REPO_ROOT / "opensre.spec"
_WINDOWS_CLEANUP_WORKER = (
    _REPO_ROOT / "surfaces" / "cli" / "lifecycle" / "windows" / "uninstall_cleanup.ps1"
)


def _release_build_job() -> dict[str, Any]:
    workflow = yaml.load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(workflow, dict)
    return workflow["jobs"]["build-binaries"]


def _release_build_step(name: str) -> dict[str, Any]:
    steps = _release_build_job()["steps"]
    return next(step for step in steps if step.get("name") == name)


def _desktop_powershell() -> Path | None:
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return candidate
    resolved = shutil.which("powershell.exe")
    return Path(resolved) if resolved else None


def test_hidden_imports_cover_runtime_discovered_tool_packages() -> None:
    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))

    assert set(INTEGRATION_TOOL_PACKAGES) <= hidden_imports
    assert "integrations.x_mcp.tools.x_mcp_tool" in hidden_imports
    assert "tools.system.work_items" in hidden_imports
    assert "tools.system.work_items.tool" in hidden_imports


def test_hidden_imports_cover_lazy_cli_command_modules() -> None:
    """CLI commands load via importlib from COMMAND_SPECS; freeze must list them.

    Without this, ``opensre _package-smoke`` (and every other top-level command)
    fails in the PyInstaller binary with ModuleNotFoundError before Click runs.
    """
    from surfaces.cli.commands.command_specs import COMMAND_SPECS

    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))
    command_modules = {spec.import_path.split(":", 1)[0] for spec in COMMAND_SPECS}

    assert command_modules <= hidden_imports
    assert "surfaces.cli.commands.package_smoke" in hidden_imports


def test_hidden_imports_exclude_non_runtime_discovery_modules() -> None:
    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))

    assert "tools.registry" not in hidden_imports


def test_hidden_imports_cover_runtime_discovered_integration_verifiers() -> None:
    hidden_imports = set(runtime_hidden_imports(_REPO_ROOT))
    verifier_modules = {
        ".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts)
        for path in (_REPO_ROOT / "integrations").glob("*/verifier.py")
    }

    assert verifier_modules <= hidden_imports


def test_required_skill_data_covers_action_and_tool_guidance() -> None:
    relative_paths = {
        path.relative_to(_REPO_ROOT).as_posix() for path in required_skill_files(_REPO_ROOT)
    }

    assert "core/agent_harness/prompts/skills/fixing-github-ci/SKILL.md" in relative_paths
    assert (
        "core/agent_harness/prompts/skills/reporting-github-ci-failures/SKILL.md" in relative_paths
    )
    assert "core/agent_harness/prompts/skills/onboarding-github-ci/SKILL.md" in relative_paths
    assert (
        "core/agent_harness/prompts/skills/onboarding-github-ci/a-analyzing-github-ci-performance/SKILL.md"
        in relative_paths
    )
    assert "integrations/github/tools/workflow/SKILL.md" in relative_paths
    assert "integrations/sentry/tools/skills/summarizing-sentry-issues/SKILL.md" in relative_paths
    assert (
        "tools/system/python_execution_tool/skills/measuring-github-star-velocity/SKILL.md"
        in relative_paths
    )


def test_release_includes_executable_skill_helpers_and_their_reference() -> None:
    skill = (
        _REPO_ROOT
        / "core/agent_harness/prompts/skills/onboarding-github-ci/b-scheduling-github-ci-fixes"
    )
    included = set(required_skill_files(_REPO_ROOT))
    assert skill / "references/script-tools.md" in included
    assert set((skill / "scripts").glob("*.py")) <= included
    assert skill / "scripts/seed_demo_repository.py" in included
    assert skill / "scripts/write_demo_evidence.py" in included


def test_required_data_covers_runtime_files_that_are_not_skill_documents() -> None:
    """Runtime file loading breaks or degrades when data is left out of the build.

    The task-plan loader requires adjacent Markdown or fails the turn outright.
    ``find_yc_api`` reads its endpoint index from a JSON file rather than a
    document, so the ``SKILL.md`` globs above do not reach it. Left out, the
    tool reports no endpoints at all instead of failing to import, which reads
    as "Yandex Cloud exposes nothing" rather than as a broken artifact.
    """
    relative_paths = {
        path.relative_to(_REPO_ROOT).as_posix() for path in required_skill_files(_REPO_ROOT)
    }

    assert "integrations/yandex_cloud/api_index.json" in relative_paths
    assert "core/agent_harness/task_plan/planning_instructions.md" not in relative_paths


def test_release_build_uses_checked_in_spec() -> None:
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    spec = _SPEC_FILE.read_text(encoding="utf-8")

    assert "uv run pyinstaller opensre.spec" in workflow
    assert "OPENSRE_PYINSTALLER_MODE: ${{ matrix.pyinstaller_mode }}" in workflow
    assert "release_manifest.py" in spec
    assert "skill_data_entries(ROOT)" in spec


def test_windows_release_build_is_strict_onedir() -> None:
    build_job = _release_build_job()
    windows = next(
        entry
        for entry in build_job["strategy"]["matrix"]["include"]
        if entry["target"] == "windows-x64"
    )

    assert windows["runner"] == "windows-latest"
    assert windows["binary_name"] == "opensre.exe"
    assert windows["archive_ext"] == "zip"
    assert windows["pyinstaller_mode"] == "onedir"

    smoke = _release_build_step("Smoke test binary (Windows)")["run"]
    assert 'Join-Path $bundlePath "${{ matrix.binary_name }}"' in smoke
    assert '$bundlePath = ".\\dist\\opensre"' in smoke
    assert "Windows onedir bundle is missing its _internal directory" in smoke
    assert "litellm\\model_prices_and_context_window_backup.json" in smoke
    assert "tools\\descriptor_index.json" in smoke
    assert "surfaces\\cli\\lifecycle\\windows\\uninstall_cleanup.ps1" in smoke
    assert "$versionStatus = $LASTEXITCODE" in smoke
    assert "$helpStatus = $LASTEXITCODE" in smoke
    assert "$packageSmokeStatus = $LASTEXITCODE" in smoke


def test_windows_release_archive_preserves_layout_and_asset_names() -> None:
    package_step = _release_build_step("Package binary archive (Windows)")
    package = package_step["run"]

    assert package_step["id"] == "package_windows"
    assert "\"opensre_$($env:TAG_NAME.TrimStart('v'))_${{ matrix.target }}\"" in package
    assert '"opensre_main_${{ matrix.target }}"' in package
    assert 'Compress-Archive -LiteralPath "dist\\opensre"' in package
    assert 'Compress-Archive -Path "dist\\${{ matrix.binary_name }}"' not in package
    assert 'Set-Content -Path "${assetBaseName}.zip.sha256"' in package
    assert (
        "Add-Content -LiteralPath $env:GITHUB_OUTPUT "
        '-Value "asset_basename=$assetBaseName"' in package
    )

    upload = _release_build_step("Upload binary archive")["with"]["path"]
    assert "opensre_*_${{ matrix.target }}.${{ matrix.archive_ext }}" in upload
    assert "opensre_*_${{ matrix.target }}.${{ matrix.archive_ext }}.sha256" in upload


@pytest.mark.skipif(
    sys.platform != "win32" or _desktop_powershell() is None,
    reason="Compress-Archive bundle-shape check requires Windows PowerShell 5.1.",
)
def test_windows_compress_archive_keeps_complete_onedir_root(tmp_path: Path) -> None:
    powershell = _desktop_powershell()
    assert powershell is not None
    bundle = tmp_path / "dist" / "opensre"
    internal = bundle / "_internal"
    worker = internal / "surfaces" / "cli" / "lifecycle" / "windows" / "uninstall_cleanup.ps1"
    worker.parent.mkdir(parents=True)
    (bundle / "opensre.exe").write_bytes(b"MZ")
    (internal / "payload.txt").write_text("required", encoding="utf-8")
    worker.write_text("exit 0\n", encoding="utf-8")
    archive = tmp_path / "opensre_main_windows-x64.zip"
    bundle_literal = "'" + str(bundle).replace("'", "''") + "'"
    archive_literal = "'" + str(archive).replace("'", "''") + "'"

    completed = subprocess.run(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Compress-Archive -LiteralPath {bundle_literal} -DestinationPath {archive_literal}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    with zipfile.ZipFile(archive) as packaged:
        entries = {name.replace("\\", "/") for name in packaged.namelist()}
    assert "opensre/opensre.exe" in entries
    assert "opensre/_internal/payload.txt" in entries
    assert "opensre/_internal/surfaces/cli/lifecycle/windows/uninstall_cleanup.ps1" in entries
    assert all(name.startswith("opensre/") for name in entries)


def test_windows_release_smokes_extracted_zip_outside_checkout() -> None:
    smoke = _release_build_step("Smoke test packaged archive (Windows)")["run"]

    assert "${{ steps.package_windows.outputs.asset_basename }}.zip" in smoke
    assert "Expand-Archive -LiteralPath $archivePath -DestinationPath $smokeRoot" in smoke
    assert '$bundlePath = Join-Path $smokeRoot "opensre"' in smoke
    assert 'Join-Path $bundlePath "${{ matrix.binary_name }}"' in smoke
    assert "litellm\\model_prices_and_context_window_backup.json" in smoke
    assert "tools\\descriptor_index.json" in smoke
    assert "surfaces\\cli\\lifecycle\\windows\\uninstall_cleanup.ps1" in smoke
    assert "Push-Location $smokeRoot" in smoke
    assert "$versionStatus = $LASTEXITCODE" in smoke
    assert "$helpStatus = $LASTEXITCODE" in smoke
    assert "$packageSmokeStatus = $LASTEXITCODE" in smoke
    assert "Remove-Item -LiteralPath $smokeRoot -Recurse -Force" in smoke


def test_spec_bakes_the_descriptor_index_into_the_bundle() -> None:
    """The frozen fallback imports every vendor module when this file is missing.

    A unit test that writes the JSON into a fake ``_MEIPASS`` cannot catch a
    spec that omits or misplaces it. Pin the PyInstaller data entry: dump at
    build time, ship under ``BAKED_INDEX_RELATIVE_PATH.parent`` so the runtime
    path ``sys._MEIPASS / tools / descriptor_index.json`` is what the bundle
    actually contains.
    """
    spec = _SPEC_FILE.read_text(encoding="utf-8")

    assert '_baked_index = ROOT / "build" / "baked" / BAKED_INDEX_RELATIVE_PATH' in spec
    assert "dump_descriptor_index(_baked_index)" in spec
    assert "datas.append((str(_baked_index), str(BAKED_INDEX_RELATIVE_PATH.parent)))" in spec
    assert BAKED_INDEX_RELATIVE_PATH.as_posix() == "tools/descriptor_index.json"


def test_release_smoke_asserts_onedir_contains_the_baked_index() -> None:
    """Unix onedir smoke must see the file on disk, not only via ``_package-smoke``.

    A unit test that writes JSON into a fake ``_MEIPASS`` cannot catch a spec
    that omits or misplaces the bake. ``_package-smoke`` fail-closed covers
    onefile (Windows), where datas live inside the archive. Onedir can assert
    the path PyInstaller materializes under ``_internal/``, same as LiteLLM.
    """
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    baked_onedir = f"./dist/opensre/_internal/{BAKED_INDEX_RELATIVE_PATH.as_posix()}"

    assert baked_onedir in workflow
    assert "_package-smoke" in workflow


def test_release_workflow_parallelizes_macos_onedir_resign() -> None:
    """Nested lib signs are independent; main binary stays serial and last."""
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert 'xargs -0 -P "$JOBS" -n 1 codesign --force --sign -' in workflow
    assert 'codesign --force --sign - "$APP_BIN"' in workflow
    assert 'if [ "$JOBS" -gt 4 ]; then' in workflow


def test_release_workflow_does_not_run_on_pull_requests() -> None:
    workflow = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    triggers = yaml.load(workflow, Loader=yaml.BaseLoader)["on"]

    assert isinstance(triggers, dict)
    assert "pull_request" not in triggers
    assert triggers["push"]["branches"] == ["main"]
    assert 'if [ "$EVENT_NAME" = "pull_request" ]; then' not in workflow
    assert 'echo "channel=pr" >> "$GITHUB_OUTPUT"' not in workflow
    assert "opensre_pr_" not in workflow


def test_release_workflow_does_not_publish_python_distributions_to_pypi() -> None:
    raw = _RELEASE_WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.load(raw, Loader=yaml.BaseLoader)

    assert "publish-python-dist" not in workflow["jobs"]
    assert "# publish-python-dist:" in raw
    assert "#       uses: pypa/gh-action-pypi-publish@" in raw


def test_infrastructure_data_excludes_the_cloudflare_worker() -> None:
    """The Cloudflare install-proxy is a JS Worker deployed via ``wrangler``.

    It never runs from the frozen binary, so bundling it only adds dead,
    non-Python weight to the release artifact.
    """
    relative_paths = {
        Path(dest) / Path(source).name for source, dest in infrastructure_data_entries(_REPO_ROOT)
    }

    assert not any("cloudflare_install_proxy" in path.parts for path in relative_paths), (
        relative_paths
    )

    assert Path("infrastructure/deployment/cloudflare_install_proxy/README.md").exists()
    assert Path("infrastructure/deployment/cloudflare_install_proxy/src/index.mjs").exists()


def test_infrastructure_data_still_covers_real_infrastructure_code() -> None:
    relative_paths = {
        Path(dest) / Path(source).name for source, dest in infrastructure_data_entries(_REPO_ROOT)
    }

    assert Path("infrastructure/deployment/packaging/release_manifest.py") in relative_paths
    assert Path("infrastructure/deployment/ec2/telegram_gateway/README.md") in relative_paths


def test_spec_bundles_infrastructure_via_the_filtered_helper() -> None:
    spec = _SPEC_FILE.read_text(encoding="utf-8")

    assert "infrastructure_data_entries(ROOT)" in spec
    assert '(str(ROOT / "infrastructure"), "infrastructure")' not in spec


def test_windows_cleanup_worker_ships_in_wheels_and_frozen_bundles() -> None:
    assert _WINDOWS_CLEANUP_WORKER.is_file()

    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "*.ps1" in package_data["surfaces.cli.lifecycle.windows"]

    spec = _SPEC_FILE.read_text(encoding="utf-8")
    assert 'collect_data_files("surfaces.cli")' in spec


def test_windows_installer_embeds_canonical_lifecycle_constants() -> None:
    source = (_REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert source.count(render_installer_constants()) == 1


def test_windows_installer_consumes_canonical_environment_names() -> None:
    source = (_REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    bindings = {
        "OpenSreReplaceExistingBinaryEnv": OPENSRE_INSTALL_REPLACE_EXISTING_BINARY_ENV,
        "OpenSreUpdateParentStartedEnv": OPENSRE_UPDATE_PARENT_STARTED_ENV,
    }

    for variable, environment_name in bindings.items():
        assert source.count(f'"{environment_name}"') == 1
        assert f"$env:{environment_name}" not in source
        assert (
            f"[System.Environment]::GetEnvironmentVariable(\n        $script:{variable}\n    )"
            in source
        )
