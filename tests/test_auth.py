"""Tests for auth injection node."""

from __future__ import annotations

from app.agent.nodes.auth import _extract_auth, inject_auth_node


def _make_config(
    configurable: dict | None = None,
) -> dict:
    """Build a minimal RunnableConfig dict."""
    return {"configurable": configurable or {}}


class TestExtractAuth:
    """Unit tests for _extract_auth."""

    def test_full_config_auth(self) -> None:
        """All values present in config — state should be ignored."""
        config = _make_config(
            {
                "langgraph_auth_user": {
                    "org_id": "org-1",
                    "identity": "user-1",
                    "email": "a@b.com",
                    "full_name": "Alice",
                    "organization_slug": "acme",
                    "token": "tok-123",
                },
                "thread_id": "thread-1",
                "run_id": "run-1",
            }
        )
        state: dict = {}

        result = _extract_auth(state, config)

        assert result["org_id"] == "org-1"
        assert result["user_id"] == "user-1"
        assert result["user_email"] == "a@b.com"
        assert result["user_name"] == "Alice"
        assert result["organization_slug"] == "acme"
        assert result["thread_id"] == "thread-1"
        assert result["run_id"] == "run-1"
        assert result["_auth_token"] == "tok-123"

    def test_empty_config_returns_empty_strings(self) -> None:
        """No config, no state — everything defaults to empty string."""
        result = _extract_auth({}, _make_config())

        assert result["org_id"] == ""
        assert result["user_id"] == ""
        assert result["user_email"] == ""
        assert result["user_name"] == ""
        assert result["organization_slug"] == ""
        assert result["thread_id"] == ""
        assert result["run_id"] == ""
        assert result["_auth_token"] == ""

    def test_state_fallback_when_config_missing(self) -> None:
        """Config auth is empty — values should fall back to state."""
        state = {
            "org_id": "state-org",
            "user_id": "state-user",
            "thread_id": "state-thread",
            "run_id": "state-run",
            "_auth_token": "state-tok",
        }
        result = _extract_auth(state, _make_config())

        assert result["org_id"] == "state-org"
        assert result["user_id"] == "state-user"
        assert result["thread_id"] == "state-thread"
        assert result["run_id"] == "state-run"
        assert result["_auth_token"] == "state-tok"

    def test_config_takes_precedence_over_state(self) -> None:
        """When both config and state have values, config wins."""
        config = _make_config(
            {
                "langgraph_auth_user": {
                    "org_id": "config-org",
                    "identity": "config-user",
                    "token": "config-tok",
                },
                "thread_id": "config-thread",
                "run_id": "config-run",
            }
        )
        state = {
            "org_id": "state-org",
            "user_id": "state-user",
            "thread_id": "state-thread",
            "run_id": "state-run",
            "_auth_token": "state-tok",
        }

        result = _extract_auth(state, config)

        assert result["org_id"] == "config-org"
        assert result["user_id"] == "config-user"
        assert result["thread_id"] == "config-thread"
        assert result["run_id"] == "config-run"
        assert result["_auth_token"] == "config-tok"

    def test_empty_string_in_config_falls_back_to_state(self) -> None:
        """Empty string in config should trigger fallback via `or`."""
        config = _make_config(
            {
                "thread_id": "",
                "run_id": "",
                "langgraph_auth_user": {"token": ""},
            }
        )
        state = {
            "thread_id": "state-thread",
            "run_id": "state-run",
            "_auth_token": "state-tok",
        }

        result = _extract_auth(state, config)

        assert result["thread_id"] == "state-thread"
        assert result["run_id"] == "state-run"
        assert result["_auth_token"] == "state-tok"

    def test_missing_configurable_key(self) -> None:
        """Config dict without 'configurable' key at all."""
        result = _extract_auth({}, {})

        assert result["org_id"] == ""
        assert result["thread_id"] == ""

    def test_partial_auth_user(self) -> None:
        """Auth user dict has only some fields."""
        config = _make_config(
            {
                "langgraph_auth_user": {
                    "org_id": "org-partial",
                    # no identity, email, full_name, token, etc.
                },
            }
        )
        result = _extract_auth({}, config)

        assert result["org_id"] == "org-partial"
        assert result["user_id"] == ""
        assert result["user_email"] == ""
        assert result["user_name"] == ""
        assert result["_auth_token"] == ""


class TestInjectAuthNode:
    """Tests for the public inject_auth_node function."""

    def test_returns_same_as_extract_auth(self) -> None:
        """inject_auth_node is a thin wrapper — output should match."""
        config = _make_config(
            {
                "langgraph_auth_user": {"org_id": "org-x", "identity": "u-x"},
                "thread_id": "t-1",
                "run_id": "r-1",
            }
        )
        state: dict = {}

        assert inject_auth_node(state, config) == _extract_auth(state, config)

    def test_return_type_is_dict(self) -> None:
        """Ensure the node returns a plain dict (LangGraph requirement)."""
        result = inject_auth_node({}, _make_config())
        assert isinstance(result, dict)
