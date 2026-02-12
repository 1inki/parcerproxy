from app.normalizer import parse_candidates


def test_parse_candidates_mixed_formats() -> None:
    text = """
    socks5://1.2.3.4:1080
    https://8.8.8.8:443
    9.9.9.9:8080
    """
    items = parse_candidates(text, source="t", default_scheme="http")
    keys = {(i.proxy_type, i.host, i.port) for i in items}
    assert ("socks5", "1.2.3.4", 1080) in keys
    assert ("https", "8.8.8.8", 443) in keys
    assert ("http", "9.9.9.9", 8080) in keys
