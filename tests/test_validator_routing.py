import pytest
from unittest.mock import AsyncMock, patch
from app.normalizer import ProxyCandidate
from app.validator import _check, ValidationResult

@pytest.fixture
def dummy_candidate():
    return ProxyCandidate(proxy_type="http", host="1.1.1.1", port=80, source="test")

@pytest.mark.asyncio
@patch("app.validator._check_http")
@patch("app.validator._check_socks")
@patch("app.validator._check_tcp_only")
async def test_validator_routing(mock_tcp, mock_socks, mock_http, dummy_candidate):
    """
    Проверка маршрутизации в зависимости от протокола прокси.
    Убеждаемся, что _check вызывает правильную функцию в зависимости от proxy_type.
    """
    # Настройка моков
    expected_res = ValidationResult(candidate=dummy_candidate, is_alive=True, latency_ms=10.0)
    mock_http.return_value = expected_res
    mock_socks.return_value = expected_res
    mock_tcp.return_value = expected_res
    
    # 1. Тест HTTP
    dummy_candidate.proxy_type = "http"
    res = await _check(dummy_candidate, 5.0)
    mock_http.assert_called_once_with(dummy_candidate, 5.0)
    mock_socks.assert_not_called()
    mock_tcp.assert_not_called()
    assert res == expected_res
    
    mock_http.reset_mock()
    
    # 2. Тест SOCKS
    dummy_candidate.proxy_type = "socks5"
    res = await _check(dummy_candidate, 5.0)
    mock_socks.assert_called_once_with(dummy_candidate, 5.0)
    mock_http.assert_not_called()
    mock_tcp.assert_not_called()
    assert res == expected_res
    
    mock_socks.reset_mock()
    
    # 3. Тест TCP ONLY (mtproto)
    dummy_candidate.proxy_type = "mtproto"
    res = await _check(dummy_candidate, 5.0)
    mock_tcp.assert_called_once_with(dummy_candidate, 5.0)
    mock_http.assert_not_called()
    mock_socks.assert_not_called()
    assert res == expected_res
    
    mock_tcp.reset_mock()
    
    # 4. Тест TCP ONLY (ss)
    dummy_candidate.proxy_type = "ss"
    res = await _check(dummy_candidate, 5.0)
    mock_tcp.assert_called_once_with(dummy_candidate, 5.0)
    assert res == expected_res
