from app.collectors.github import PROXYISH_PATH_RE


def test_proxyish_path_filter() -> None:
    assert PROXYISH_PATH_RE.search("configs/socks5_list.txt")
    assert PROXYISH_PATH_RE.search("src/mtproto_proxy.py")
    assert not PROXYISH_PATH_RE.search("docs/changelog.md")
