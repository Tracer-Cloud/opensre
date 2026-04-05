from __future__ import annotations

from types import SimpleNamespace

from app.cli.tests import interactive
from app.cli.tests.catalog import TestCatalog as Catalog
from app.cli.tests.catalog import TestCatalogItem as CatalogItem
from app.cli.tests.discover import load_test_catalog


def test_choose_interactive_item_returns_single_match_without_extra_prompt(monkeypatch) -> None:
    catalog = Catalog(
        items=(
            CatalogItem(
                id="make:test-cov",
                kind="make_target",
                display_name="Coverage Suite",
                description="Run the coverage suite.",
                command=("make", "test-cov"),
                tags=("ci-safe",),
            ),
        )
    )
    selected_prompts: list[str] = []

    monkeypatch.setattr(interactive, "_choose_category", lambda: "ci-safe")

    def _mock_select_item(items, *, prompt: str, allow_back: bool = False):
        _ = allow_back
        selected_prompts.append(prompt)
        return items[0]

    monkeypatch.setattr(interactive, "_select_item", _mock_select_item)

    item, auto_selected = interactive.choose_interactive_item(catalog)

    assert item.id == "make:test-cov"
    assert auto_selected is True
    assert selected_prompts == []


def test_choose_interactive_item_prompts_when_multiple_matches_exist(monkeypatch) -> None:
    catalog = load_test_catalog()
    selected_prompts: list[str] = []
    selected_item_ids: list[list[str]] = []

    monkeypatch.setattr(interactive, "_choose_category", lambda: "ci-safe")

    def _mock_select_item(items, *, prompt: str, allow_back: bool = False):
        _ = allow_back
        selected_prompts.append(prompt)
        selected_item_ids.append([item.id for item in items])
        return items[0]

    monkeypatch.setattr(interactive, "_select_item", _mock_select_item)

    item, auto_selected = interactive.choose_interactive_item(catalog)

    assert item.id == selected_item_ids[0][0]
    assert auto_selected is False
    assert selected_prompts == ["Choose a test or suite:"]
    assert "make:test-cov" in selected_item_ids[0]


def test_choose_interactive_item_raises_on_empty_filter(monkeypatch) -> None:
    catalog = Catalog(items=())

    monkeypatch.setattr(interactive, "_choose_category", lambda: "rca")

    try:
        interactive.choose_interactive_item(catalog)
    except ValueError as exc:
        assert "No tests matched the selected category." in str(exc)
    else:
        raise AssertionError("Expected choose_interactive_item to reject empty categories")


def test_choose_interactive_item_reselects_category_after_escape(monkeypatch) -> None:
    catalog = Catalog(
        items=(
            CatalogItem(
                id="make:test-cov",
                kind="make_target",
                display_name="Coverage Suite",
                description="Run the coverage suite.",
                command=("make", "test-cov"),
                tags=("ci-safe",),
            ),
            CatalogItem(
                id="make:test-full",
                kind="make_target",
                display_name="Full Suite",
                description="Run the full suite.",
                command=("make", "test-full"),
                tags=("ci-safe",),
            ),
            CatalogItem(
                id="make:demo",
                kind="make_target",
                display_name="Prefect ECS Demo",
                description="Run the demo.",
                command=("make", "demo"),
                tags=("demo",),
            ),
        )
    )
    category_choices = iter(["ci-safe", "demo"])
    selected_prompts: list[str] = []

    monkeypatch.setattr(interactive, "_choose_category", lambda: next(category_choices))

    def _mock_select_item(items, *, prompt: str, allow_back: bool = False):
        _ = allow_back
        selected_prompts.append(prompt)
        if len(selected_prompts) == 1:
            raise interactive._GoBack
        return items[0]

    monkeypatch.setattr(interactive, "_select_item", _mock_select_item)

    item, _ = interactive.choose_interactive_item(catalog)

    assert item.id == "make:demo"
    assert selected_prompts == ["Choose a test or suite:"]


def test_choose_interactive_item_returns_to_parent_list_after_escape(monkeypatch) -> None:
    suite = CatalogItem(
        id="suite:demo",
        kind="suite",
        display_name="Demo Suite",
        description="A grouped demo suite.",
        tags=("demo",),
        children=(
            CatalogItem(
                id="scenario:demo:first",
                kind="scenario",
                display_name="First scenario",
                description="First child.",
                command=("make", "demo"),
                tags=("demo",),
            ),
            CatalogItem(
                id="scenario:demo:second",
                kind="scenario",
                display_name="Second scenario",
                description="Second child.",
                command=("make", "demo"),
                tags=("demo",),
            ),
        ),
    )
    leaf = CatalogItem(
        id="make:demo",
        kind="make_target",
        display_name="Standalone Demo",
        description="Run the demo.",
        command=("make", "demo"),
        tags=("demo",),
    )
    catalog = Catalog(items=(suite, leaf))
    selected_prompts: list[str] = []

    monkeypatch.setattr(interactive, "_choose_category", lambda: "demo")

    def _mock_select_item(items, *, prompt: str, allow_back: bool = False):
        _ = (items, allow_back)
        selected_prompts.append(prompt)
        if prompt == "Choose a test or suite:" and len(selected_prompts) == 1:
            return suite
        if prompt == "Select a scenario from Demo Suite:":
            raise interactive._GoBack
        return leaf

    monkeypatch.setattr(interactive, "_select_item", _mock_select_item)

    item, _ = interactive.choose_interactive_item(catalog)

    assert item.id == "make:demo"
    assert selected_prompts == [
        "Choose a test or suite:",
        "Select a scenario from Demo Suite:",
        "Choose a test or suite:",
    ]


def test_run_interactive_picker_returns_zero_on_escape(monkeypatch) -> None:
    catalog = load_test_catalog()

    monkeypatch.setattr(interactive, "_require_interactive_dependencies", lambda: None)
    monkeypatch.setattr(interactive.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive, "choose_interactive_item", lambda _catalog: (_ for _ in ()).throw(KeyboardInterrupt()))  # noqa: E501

    assert interactive.run_interactive_picker(catalog) == 0


def test_run_interactive_picker_returns_to_selection_after_escape_from_confirm(monkeypatch) -> None:
    first = CatalogItem(
        id="make:test-cov",
        kind="make_target",
        display_name="Coverage Suite",
        description="Run coverage.",
        command=("make", "test-cov"),
        tags=("ci-safe",),
    )
    second = CatalogItem(
        id="make:test-full",
        kind="make_target",
        display_name="Full Suite",
        description="Run full tests.",
        command=("make", "test-full"),
        tags=("ci-safe",),
    )
    selections = iter([(first, False), (second, False)])

    monkeypatch.setattr(interactive, "_require_interactive_dependencies", lambda: None)
    monkeypatch.setattr(interactive.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive.sys, "stdout", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(interactive, "choose_interactive_item", lambda _catalog: next(selections))

    confirm_calls = 0

    def _mock_confirm(item):
        _ = item
        nonlocal confirm_calls
        confirm_calls += 1
        if confirm_calls == 1:
            raise interactive._GoBack
        return True

    monkeypatch.setattr(interactive, "_confirm_run", _mock_confirm)
    monkeypatch.setattr(interactive, "run_catalog_item", lambda item: 7 if item.id == "make:test-full" else 1)

    assert interactive.run_interactive_picker(Catalog(items=(first, second))) == 7
