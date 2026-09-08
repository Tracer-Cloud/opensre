"""Contracts for deterministic code and data inputs in frozen releases."""

from __future__ import annotations

from pathlib import Path

import yaml

from infrastructure.deployment.packaging.release_manifest import (
    infrastructure_data_entries,
    required_skill_files,
    runtime_hidden_imports,
)
from tools.registry_discovery import INTEGRATION_TOOL_PACKAGES
from tools.registry_index import BAKED_INDEX_RELATIVE_PATH

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_SPEC_FILE = _REPO_ROOT / "opensre.spec"


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

    assert "core/agent_harness/prompts/skills/architecture_audit/SKILL.md" in relative_paths
    assert "core/agent_harness/prompts/skills/github_ci_health/SKILL.md" in relative_paths
    assert "core/agent_harness/prompts/skills/onboarding_cicd_fix/SKILL.md" in relative_paths
    assert (
        "core/agent_harness/prompts/skills/onboarding_cicd_fix/a_local_analysis/SKILL.md"
        in relative_paths
    )
    assert (
        "core/agent_harness/prompts/skills/architecture_audit/architecture_audit_report.md"
        in relative_paths
    )
    assert "integrations/github/tools/workflow/SKILL.md" in relative_paths
    assert "integrations/sentry/tools/skills/sentry-summary/SKILL.md" in relative_paths
    assert (
        "tools/system/python_execution_tool/skills/github-star-velocity/SKILL.md" in relative_paths
    )


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


def test_release_workflow_publishes_python_distributions_to_pypi() -> None:
    workflow = yaml.load(_RELEASE_WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    publish_job = workflow["jobs"]["publish-python-dist"]

    assert publish_job["needs"] == ["build-python-dist", "publish-release"]
    assert publish_job["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/p/opensre",
    }
    assert publish_job["permissions"] == {"id-token": "write"}

    download_step, publish_step = publish_job["steps"]
    assert download_step["uses"] == (
        "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    )
    assert download_step["with"] == {
        "name": "release-python-dist",
        "path": "dist",
    }
    assert publish_step["uses"] == (
        "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"
    )
    assert publish_step["with"]["skip-existing"] == "true"
    assert "password" not in publish_step.get("with", {})


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
