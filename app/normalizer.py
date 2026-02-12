from __future__ import annotations

import re
from dataclasses import dataclass


PROXY_RE = re.compile(
    r"(?P<scheme>socks5|socks4|http|https|mtproto|ss)://(?P<host>[a-zA-Z0-9_.-]+):(?P<port>\d{2,5})",
    re.IGNORECASE,
)
FALLBACK_RE = re.compile(r"(?P<host>(?:\d{1,3}\.){3}\d{1,3}):(?P<port>\d{2,5})")


@dataclass(slots=True)
class ProxyCandidate:
    proxy_type: str
    host: str
    port: int
    source: str


def parse_candidates(text: str, source: str, default_scheme: str = "http") -> list[ProxyCandidate]:
    out: list[ProxyCandidate] = []
    seen = set()
    for m in PROXY_RE.finditer(text):
        proxy_type = m.group("scheme").lower()
        host = m.group("host")
        port = int(m.group("port"))
        key = (proxy_type, host, port)
        if key in seen:
            continue
        seen.add(key)
        out.append(ProxyCandidate(proxy_type=proxy_type, host=host, port=port, source=source))

    for m in FALLBACK_RE.finditer(text):
        proxy_type = default_scheme.lower()
        host = m.group("host")
        port = int(m.group("port"))
        key = (proxy_type, host, port)
        if key in seen:
            continue
        seen.add(key)
        out.append(ProxyCandidate(proxy_type=proxy_type, host=host, port=port, source=source))

    return out
