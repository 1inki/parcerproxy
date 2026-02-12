from app.normalizer import ProxyCandidate
from app.pipeline import _prepare_candidates


def test_prepare_candidates_dedup_and_limit() -> None:
    items = [
        ProxyCandidate("socks5", "1.1.1.1", 1080, "a"),
        ProxyCandidate("socks5", "1.1.1.1", 1080, "b"),
        ProxyCandidate("mtproto", "2.2.2.2", 443, "a"),
        ProxyCandidate("http", "3.3.3.3", 80, "a"),
        ProxyCandidate("ss", "4.4.4.4", 8388, "a"),
    ]
    out = _prepare_candidates(items, limit=3)
    assert len(out) == 3
    keys = {(x.proxy_type, x.host, x.port) for x in out}
    assert len(keys) == 3
