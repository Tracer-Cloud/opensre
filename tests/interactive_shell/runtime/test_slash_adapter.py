from surfaces.interactive_shell.runtime.slash_adapter import headless_slash_ports


def test_model_connection_listing_suspends_prompt_without_queuing_another_turn(monkeypatch):
    from rich.console import Console

    from surfaces.interactive_shell.runtime import slash_adapter
    from surfaces.interactive_shell.session import Session

    ports = slash_adapter.ReplSlashPorts()
    session = Session()
    rendered = []

    def suspend(current, render):
        assert current is session
        rendered.append(True)
        return render()

    monkeypatch.setattr(ports, "tty_interactive", lambda: True)
    monkeypatch.setattr(slash_adapter, "with_exclusive_output", suspend)
    monkeypatch.setattr(slash_adapter, "dispatch_slash", lambda *_args, **_kwargs: True)
    assert ports.dispatch(
        "/login list", session=session, console=Console(), confirm_fn=None, is_tty=True
    )
    assert rendered == [True]


def test_headless_slash_messages_do_not_contain_rich_markup() -> None:
    ports = headless_slash_ports()

    messages = [
        ports.format_turn_outcome("/health", ok=True),
        ports.format_turn_outcome("/health", ok=False),
    ]

    for message in messages:
        assert "[" not in message
        assert "]" not in message
