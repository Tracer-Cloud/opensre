from app.utils.ingest_delivery import create_investigation_and_attach_url


def test_create_investigation_and_attach_url_success(mocker):
    """
    Happy path:
    - First ingest returns investigation_id
    - Second ingest succeeds
    - URL is generated correctly
    """
    mock_send = mocker.patch("app.utils.ingest_delivery.send_ingest")
    mock_send.side_effect = ["inv-123", None]

    mock_get_url = mocker.patch(
        "app.utils.ingest_delivery.get_investigation_url",
        return_value="http://test/url",
    )

    state = {"organization_slug": "org"}

    inv_id, url = create_investigation_and_attach_url(
        state,
        slack_message="test message",
        summary="summary",
    )

    assert inv_id == "inv-123"
    assert url == "http://test/url"

    # Ensure both ingests happened
    assert mock_send.call_count == 2

    # Validate URL generation call
    mock_get_url.assert_called_once_with("org", "inv-123")


def test_second_ingest_failure_does_not_crash(mocker):
    """
    First ingest succeeds, second ingest raises exception.
    Function should NOT raise and should still return values.
    """
    mock_send = mocker.patch("app.utils.ingest_delivery.send_ingest")
    mock_send.side_effect = ["inv-123", Exception("boom")]

    mocker.patch(
        "app.utils.ingest_delivery.get_investigation_url",
        return_value="http://test/url",
    )

    state = {"organization_slug": "org"}

    inv_id, url = create_investigation_and_attach_url(
        state,
        slack_message="test message",
        summary="summary",
    )

    assert inv_id == "inv-123"
    assert url == "http://test/url"

    # Both calls attempted
    assert mock_send.call_count == 2


def test_no_investigation_id_skips_second_ingest(mocker):
    """
    If first ingest returns None:
    - Second ingest should NOT be called
    """
    mock_send = mocker.patch("app.utils.ingest_delivery.send_ingest")
    mock_send.return_value = None

    mock_get_url = mocker.patch(
        "app.utils.ingest_delivery.get_investigation_url",
        return_value=None,
    )

    state = {"organization_slug": "org"}

    inv_id, url = create_investigation_and_attach_url(
        state,
        slack_message="test message",
        summary="summary",
    )

    assert inv_id is None
    assert url is None

    # Only first call should happen
    assert mock_send.call_count == 1

    mock_get_url.assert_called_once_with("org", None)


def test_first_ingest_exception_handled(mocker):
    """
    If first ingest itself raises exception:
    - Should not crash
    - Should return (None, url=None)
    """
    mock_send = mocker.patch("app.utils.ingest_delivery.send_ingest")
    mock_send.side_effect = Exception("failure")

    mock_get_url = mocker.patch(
        "app.utils.ingest_delivery.get_investigation_url",
        return_value=None,
    )

    state = {"organization_slug": "org"}

    inv_id, url = create_investigation_and_attach_url(
        state,
        slack_message="test message",
        summary="summary",
    )

    assert inv_id is None
    assert url is None

    # Only one attempt made
    assert mock_send.call_count == 1

    mock_get_url.assert_called_once_with("org", None)
