import click

from app.cli.context import is_debug, is_json_output, is_verbose, is_yes


def test_is_json_output_no_context() -> None:
    assert is_json_output() is False


def test_is_json_output_with_context() -> None:
    cmd = click.Command("test")
    with click.Context(cmd, obj={"json": True}):
        assert is_json_output() is True

    with click.Context(cmd, obj={"json": False}):
        assert is_json_output() is False


def test_is_verbose_no_context() -> None:
    assert is_verbose() is False


def test_is_verbose_with_context() -> None:
    cmd = click.Command("test")
    with click.Context(cmd, obj={"verbose": True}):
        assert is_verbose() is True

    with click.Context(cmd, obj={"verbose": False}):
        assert is_verbose() is False


def test_is_debug_no_context() -> None:
    assert is_debug() is False


def test_is_debug_with_context() -> None:
    cmd = click.Command("test")
    with click.Context(cmd, obj={"debug": True}):
        assert is_debug() is True

    with click.Context(cmd, obj={"debug": False}):
        assert is_debug() is False


def test_is_yes_no_context() -> None:
    assert is_yes() is False


def test_is_yes_with_context() -> None:
    cmd = click.Command("test")
    with click.Context(cmd, obj={"yes": True}):
        assert is_yes() is True

    with click.Context(cmd, obj={"yes": False}):
        assert is_yes() is False


def test_nested_context_reads_from_root() -> None:
    parent_cmd = click.Command("parent")
    child_cmd = click.Command("child")

    with (
        click.Context(parent_cmd, obj={"json": True, "verbose": True}) as parent_ctx,
        click.Context(child_cmd, parent=parent_ctx, obj={"json": False, "debug": True}),
    ):
        # The context helpers should traverse up to the parent
        # and read `obj` from the root context.
        assert is_json_output() is True
        assert is_verbose() is True
        # The root context does not have `debug` set to True
        assert is_debug() is False
