"""Ensure AgentState (TypedDict) and AgentStateModel (Pydantic) stay in sync.

If this test fails, a field was added/removed in one definition but not the other.
Fix the drift by updating both classes in app/state/agent_state.py.
"""

from app.state.agent_state import AgentState, AgentStateModel


def _typed_dict_keys(td: type) -> set[str]:
    """Return all annotated keys from a TypedDict (including inherited ones)."""
    keys: set[str] = set()
    for base in reversed(td.__mro__):
        keys.update(getattr(base, "__annotations__", {}).keys())
    return keys


def _pydantic_keys(model: type) -> set[str]:
    """Return all field names from a Pydantic model, resolving aliases back to field names."""
    keys: set[str] = set()
    for name, field_info in model.model_fields.items():
        # If a field has an alias (e.g. alias="_auth_token"), use the alias as the
        # canonical key because that is what AgentState declares.
        alias = field_info.alias
        keys.add(alias if alias is not None else name)
    return keys


def test_agent_state_and_model_share_same_keys() -> None:
    """AgentState and AgentStateModel must declare exactly the same set of field keys."""
    typed_dict_keys = _typed_dict_keys(AgentState)
    pydantic_keys = _pydantic_keys(AgentStateModel)

    only_in_typed_dict = typed_dict_keys - pydantic_keys
    only_in_pydantic = pydantic_keys - typed_dict_keys

    assert not only_in_typed_dict, (
        f"Fields present in AgentState (TypedDict) but missing from AgentStateModel: "
        f"{sorted(only_in_typed_dict)}"
    )
    assert not only_in_pydantic, (
        f"Fields present in AgentStateModel (Pydantic) but missing from AgentState: "
        f"{sorted(only_in_pydantic)}"
    )
