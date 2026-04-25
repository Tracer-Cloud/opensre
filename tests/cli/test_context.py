from __future__ import annotations

from unittest.mock import MagicMock, patch

import click

from app.cli.context import is_debug, is_json_output, is_verbose, is_yes


def test_is_json_output_true() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"json": True}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_json_output() is True


def test_is_json_output_false() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"json": False}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_json_output() is False


def test_is_verbose_true() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"verbose": True}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_verbose() is True


def test_is_verbose_false() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"verbose": False}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_verbose() is False


def test_is_debug_true() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"debug": True}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_debug() is True


def test_is_debug_false() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"debug": False}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_debug() is False


def test_is_yes_true() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"yes": True}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_yes() is True


def test_is_yes_false() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = {"yes": False}
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_yes() is False


def test_no_context() -> None:
    with patch("click.get_current_context", return_value=None) as mock_get_ctx:
        assert is_json_output() is False
        assert is_verbose() is False
        assert is_debug() is False
        assert is_yes() is False
        # Verify that we call it with silent=True so it doesn't raise RuntimeError
        mock_get_ctx.assert_called_with(silent=True)


def test_root_traversal() -> None:
    root_ctx = MagicMock(spec=click.Context)
    root_ctx.parent = None
    root_ctx.obj = {"json": True}

    child_ctx = MagicMock(spec=click.Context)
    child_ctx.parent = root_ctx
    child_ctx.obj = {"json": False}  # Should be ignored

    with patch("click.get_current_context", return_value=child_ctx):
        assert is_json_output() is True


def test_none_obj() -> None:
    mock_ctx = MagicMock(spec=click.Context)
    mock_ctx.parent = None
    mock_ctx.obj = None
    with patch("click.get_current_context", return_value=mock_ctx):
        assert is_json_output() is False
        assert is_verbose() is False
        assert is_debug() is False
        assert is_yes() is False
