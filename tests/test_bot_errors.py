import pytest

pytest.importorskip("openpyxl")
telegram_error = pytest.importorskip("telegram.error")
BadRequest = telegram_error.BadRequest

from app.bot import _is_not_modified_error


def test_not_modified_error_detection() -> None:
    exc = BadRequest("Message is not modified: specified new message content")
    assert _is_not_modified_error(exc) is True


def test_not_modified_error_detection_negative() -> None:
    exc = BadRequest("chat not found")
    assert _is_not_modified_error(exc) is False
