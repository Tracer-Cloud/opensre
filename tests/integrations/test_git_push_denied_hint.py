"""A push refused by GitHub tells the user what the credential lacks; other failures stay as they are."""

from __future__ import annotations

from integrations.git.local import push_failure_message

_DENIED = (
    "remote: Permission to YauhenBichel/Test-Project-Booking-API.git denied to YauhenBichel.\n"
    "fatal: unable to access 'https://github.com/YauhenBichel/Test-Project-Booking-API.git/': "
    "The requested URL returned error: 403"
)


def test_a_refused_push_names_the_missing_permission() -> None:
    # Arrange / Act
    message = push_failure_message("origin/opensre-ci-test-2", _DENIED)

    # Assert: git's own words stay first, then what to grant
    assert message.startswith("git push to origin/opensre-ci-test-2 failed: remote: Permission to")
    assert '"Contents: read and write"' in message and "fine-grained token" in message


def test_any_other_push_failure_keeps_gits_words_only() -> None:
    # Arrange / Act
    message = push_failure_message("origin/main", " ! [rejected] main -> main (fetch first)\n")

    # Assert
    assert message == "git push to origin/main failed: ! [rejected] main -> main (fetch first)"
    assert "Contents" not in message
