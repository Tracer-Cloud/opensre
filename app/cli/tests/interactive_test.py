from __future__ import annotations

from app.cli.tests import interactive
from app.cli.tests.discover import load_test_catalog


def test_choose_interactive_item_returns_single_filtered_match(monkeypatch) -> None:
    catalog = load_test_catalog()
    selected_prompts: list[str] = []

    monkeypatch.setattr(interactive, "_choose_category", lambda: "ci-safe")
    monkeypatch.setattr(interactive, "_prompt_search_term", lambda: "coverage")

    def _mock_select_item(items, *, prompt: str):
        selected_prompts.append(prompt)
        return items[0]

    monkeypatch.setattr(interactive, "_select_item", _mock_select_item)

    item = interactive.choose_interactive_item(catalog)

    assert item.id == "make:test-cov"
    assert selected_prompts == []


def test_choose_interactive_item_prompts_when_multiple_matches_exist(monkeypatch) -> None:
    catalog = load_test_catalog()
    selected_prompts: list[str] = []
    selected_item_ids: list[list[str]] = []

    monkeypatch.setattr(interactive, "_choose_category", lambda: "ci-safe")
    monkeypatch.setattr(interactive, "_prompt_search_term", lambda: "")

    def _mock_select_item(items, *, prompt: str):
        selected_prompts.append(prompt)
        selected_item_ids.append([item.id for item in items])
        return items[0]

    monkeypatch.setattr(interactive, "_select_item", _mock_select_item)

    item = interactive.choose_interactive_item(catalog)

    assert item.id == selected_item_ids[0][0]
    assert selected_prompts == ["Choose a test or suite:"]
    assert "make:test-cov" in selected_item_ids[0]


def test_choose_interactive_item_raises_on_empty_filter(monkeypatch) -> None:
    catalog = load_test_catalog()

    monkeypatch.setattr(interactive, "_choose_category", lambda: "rca")
    monkeypatch.setattr(interactive, "_prompt_search_term", lambda: "definitely-no-match")

    try:
        interactive.choose_interactive_item(catalog)
    except ValueError as exc:
        assert "No tests matched" in str(exc)
    else:
        raise AssertionError("Expected choose_interactive_item to reject empty filter results")
