"""Which providers each delivery consumer actually supports, stated once.

``Provider`` offers six members to every caller, and no caller supports all
six. Each consumer picks a subset, and until now those subsets lived in prose:
the enum docstring says consumers "define their own documented subset rather
than exposing a choice that would silently fail".

This file is that documentation as an assertion. Each expectation below is
compared for exact equality against what the code can actually reach, so a new
provider, a removed branch, or a consumer quietly widening its support fails
here rather than at a user's delivery time.
"""

from __future__ import annotations

from infrastructure.scheduling.scheduler import delivery
from infrastructure.scheduling.scheduler.types import Provider

#: Every member the vocabulary offers a caller.
ALL_PROVIDERS = frozenset(Provider)

#: What the installed delivery bundle has an adapter for. Anything else resolves
#: to no adapter and fails the task with "Unsupported provider".
EXECUTOR_DELIVERS = frozenset(
    {
        Provider.TELEGRAM,
        Provider.SLACK,
        Provider.DISCORD,
        Provider.ROCKETCHAT,
        Provider.EMAIL,
        Provider.INTERACTIVE_SHELL,
    }
)

#: What ``delivery._DELIVERY_SPECS`` knows how to check readiness and print
#: setup hints for. Narrower than what the executor can send to.
DELIVERY_SPECS_COVER = frozenset({Provider.TELEGRAM, Provider.SLACK, Provider.ROCKETCHAT})


def test_the_executor_delivers_to_exactly_these_providers() -> None:
    """The installed adapter bundle is the real answer to "can this be delivered?"."""
    # Arrange / Act: the composition root assembles exactly the deliverable set.
    from bootstrap.adapters import scheduled_delivery_adapters

    reachable = scheduled_delivery_adapters().providers()

    # Assert
    assert reachable == EXECUTOR_DELIVERS


def test_buzz_is_offered_by_the_vocabulary_and_refused_by_the_executor() -> None:
    """A scheduled task set to ``buzz`` fails at delivery, not at creation.

    Cron delivery does not support ``Provider.BUZZ``, so the enum offers a
    cron task a choice its executor will refuse
    with "Unsupported provider". Pinned so the gap stays visible; delete this
    test when the executor grows a buzz branch or the vocabularies split.
    """
    # Assert
    assert Provider.BUZZ in ALL_PROVIDERS
    assert Provider.BUZZ not in EXECUTOR_DELIVERS


def test_the_spec_list_is_narrower_than_what_the_executor_can_send_to() -> None:
    """Readiness and setup hints cover fewer providers than delivery reaches.

    ``interactive_shell`` is excluded on purpose — it is a local inbox, not a
    chat/webhook channel, so digest CLIs must not offer it. ``discord`` is not:
    the executor delivers to it while ``any_delivery_ready`` and the setup hints
    do not know it exists.
    """
    # Arrange / Act
    specs = frozenset(delivery.SUPPORTED_DELIVERY_PROVIDERS)

    # Assert
    assert specs == DELIVERY_SPECS_COVER
    assert Provider.INTERACTIVE_SHELL in EXECUTOR_DELIVERS - specs
    assert Provider.DISCORD in EXECUTOR_DELIVERS - specs
