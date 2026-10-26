"""Ctrl+P and Alt/Option+P toggle the plan overlay only while a plan is on screen."""

from __future__ import annotations

from surfaces.interactive_shell.ui.hooks import install_plan_expand_key_bindings


class _FakeState:
    def __init__(self) -> None:
        self.plan_expanded = False

    def toggle_plan_expanded(self) -> None:
        self.plan_expanded = not self.plan_expanded


def _binding_keys(kb) -> set[tuple[str, ...]]:
    return {tuple(str(key) for key in binding.keys) for binding in kb.bindings}


def test_expand_bindings_include_alt_and_ctrl_p() -> None:
    state = _FakeState()
    kb = install_plan_expand_key_bindings(state, lambda: True, lambda: None)
    keys = _binding_keys(kb)
    assert ("Keys.Escape", "p") in keys
    assert ("Keys.ControlP",) in keys


def test_ctrl_p_toggles_expansion_and_redraws() -> None:
    state = _FakeState()
    redraws: list[bool] = []
    kb = install_plan_expand_key_bindings(state, lambda: True, lambda: redraws.append(True))
    handler = next(
        binding.handler
        for binding in kb.bindings
        if tuple(str(k) for k in binding.keys) == ("Keys.ControlP",)
    )

    handler(None)
    handler(None)

    assert state.plan_expanded is False
    assert redraws == [True, True]


def test_binding_is_gated_on_a_plan_being_present() -> None:
    state = _FakeState()
    kb = install_plan_expand_key_bindings(state, lambda: False, lambda: None)

    assert all(binding.filter() is False for binding in kb.bindings)
