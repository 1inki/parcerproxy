import base64
import json
import pytest

from app.normalizer import parse_candidates, ProxyCandidate

def test_standard_format():
    """Проверка стандартных HTTP и SOCKS форматов с аутентификацией и дедупликацией."""
    text = '''
        http://1.1.1.1:8080
        socks5://user:pass@2.2.2.2:1080
        http://1.1.1.1:8080
    '''
    res = parse_candidates(text, source="test")
    # Ожидаем два уникальных кандидата
    assert len(res) == 2
    
    assert res[0].proxy_type == "http"
    assert res[0].host == "1.1.1.1"
    assert res[0].port == 8080
    assert res[0].source == "test"
    
    assert res[1].proxy_type == "socks5"
    assert res[1].host == "2.2.2.2"
    assert res[1].port == 1080

def test_shadowsocks_uri():
    """Проверка раскодирования SS URI."""
    # "aes-256-gcm:password@3.3.3.3:8388"
    raw = b"aes-256-gcm:password@3.3.3.3:8388"
    b64 = base64.b64encode(raw).decode('utf-8')
    text = f"ss://{b64}#MyServer\n"
    
    res = parse_candidates(text, source="test")
    assert len(res) == 1
    assert res[0].proxy_type == "ss"
    assert res[0].host == "3.3.3.3"
    assert res[0].port == 8388

def test_vmess_uri():
    """Проверка раскодирования VMess URI."""
    config = {"add": "4.4.4.4", "port": 443, "id": "uuid"}
    b64 = base64.b64encode(json.dumps(config).encode('utf-8')).decode('utf-8')
    text = f"vmess://{b64}"
    
    res = parse_candidates(text, source="test")
    assert len(res) == 1
    assert res[0].proxy_type == "vmess"
    assert res[0].host == "4.4.4.4"
    assert res[0].port == 443

def test_json_format():
    """Проверка извлечения данных из JSON-конфигов."""
    text = '''
    {
        "server": "5.5.5.5",
        "server_port": 5000,
        "password": "mypassword"
    }'''
    res = parse_candidates(text, source="test")
    assert len(res) == 1
    assert res[0].proxy_type == "ss"
    assert res[0].host == "5.5.5.5"
    assert res[0].port == 5000

def test_space_tab_format():
    """Проверка форматов с пробелами или табуляцией."""
    text = "6.6.6.6 6000\n7.7.7.7\t7000"
    res = parse_candidates(text, source="test", default_scheme="https")
    assert len(res) == 2
    assert res[0].proxy_type == "https"
    assert res[0].host == "6.6.6.6"
    assert res[0].port == 6000
    assert res[1].proxy_type == "https"
    assert res[1].host == "7.7.7.7"
    assert res[1].port == 7000

def test_edge_cases():
    """Проверка пустых и мусорных строк."""
    text = "Невалидный текст http://bad.port ss://invalid_base64_string vmess://bad {"
    res = parse_candidates(text, source="test")
    # Должен ничего не найти и проглотить исключения
    assert len(res) == 0
