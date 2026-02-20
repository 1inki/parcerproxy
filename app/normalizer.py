from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass


# --- Скомпилированные RegEx паттерны ---

# Стандартный формат: protocol://[user:pass@]host:port
PROXY_RE = re.compile(
    r"(?P<scheme>socks5|socks4|http|https|mtproto|ss)"
    r"://"
    r"(?:[\w.~%-]+:[\w.~%-]+@)?"   # опциональная аутентификация user:pass@
    r"(?P<host>[a-zA-Z0-9_.-]+)"
    r":(?P<port>\d{2,5})",
    re.IGNORECASE,
)

# Голый ip:port (без схемы)
FALLBACK_RE = re.compile(r"(?P<host>(?:\d{1,3}\.){3}\d{1,3}):(?P<port>\d{2,5})")

# Shadowsocks URI: ss://base64...#tag
SS_URI_RE = re.compile(r"ss://([A-Za-z0-9+/=]+)(?:#\S+)?")

# VMess URI: vmess://base64...
VMESS_URI_RE = re.compile(r"vmess://([A-Za-z0-9+/=]+)")

# JSON-конфиг Shadowsocks: {"server": "host", "server_port": port}
JSON_SERVER_RE = re.compile(
    r'"server"\s*:\s*"(?P<host>[^"]+)"\s*,\s*"server_port"\s*:\s*(?P<port>\d{2,5})'
)

# Пробельно-табличный формат: 1.2.3.4 1080 или 1.2.3.4\t1080
SPACE_RE = re.compile(
    r"^(?P<host>\d{1,3}(?:\.\d{1,3}){3})[\s\t]+(?P<port>\d{2,5})$",
    re.MULTILINE,
)


@dataclass(slots=True)
class ProxyCandidate:
    proxy_type: str
    host: str
    port: int
    source: str


def _add_candidate(
    out: list[ProxyCandidate],
    seen: set,
    proxy_type: str,
    host: str,
    port: int,
    source: str,
) -> None:
    """Вспомогательная функция: добавляет кандидата если он ещё не встречался."""
    key = (proxy_type, host, port)
    if key in seen:
        return
    seen.add(key)
    out.append(ProxyCandidate(proxy_type=proxy_type, host=host, port=port, source=source))


def _safe_b64_decode(data: str) -> bytes:
    """
    Безопасное base64 декодирование с дополнением padding.
    Многие URI содержат невалидный padding — исправляем автоматически.
    """
    # Убираем возможные пробелы и переводы строк
    data = data.strip()
    # Дополняем padding до кратности 4
    missing_padding = len(data) % 4
    if missing_padding:
        data += "=" * (4 - missing_padding)
    return base64.b64decode(data)


def _parse_ss_uri(text: str, source: str, out: list[ProxyCandidate], seen: set) -> None:
    """
    Парсинг Shadowsocks URI формата: ss://base64(method:password@host:port)
    Base64 содержимое декодируется и из него извлекается host:port.
    """
    for m in SS_URI_RE.finditer(text):
        try:
            decoded = _safe_b64_decode(m.group(1)).decode("utf-8", errors="replace")
            # Формат: method:password@host:port
            if "@" in decoded:
                server_part = decoded.rsplit("@", 1)[1]
            else:
                server_part = decoded

            # Извлекаем host:port из серверной части
            if ":" in server_part:
                host, port_str = server_part.rsplit(":", 1)
                port = int(port_str)
                _add_candidate(out, seen, "ss", host, port, source)
        except Exception:
            # Битые данные — пропускаем, не ломаем парсинг
            continue


def _parse_vmess_uri(text: str, source: str, out: list[ProxyCandidate], seen: set) -> None:
    """
    Парсинг VMess URI формата: vmess://base64(JSON)
    Base64 содержимое декодируется как JSON с полями "add" (хост) и "port".
    """
    for m in VMESS_URI_RE.finditer(text):
        try:
            decoded = _safe_b64_decode(m.group(1)).decode("utf-8", errors="replace")
            config = json.loads(decoded)
            host = str(config.get("add", "")).strip()
            port = int(config.get("port", 0))
            if host and port > 0:
                _add_candidate(out, seen, "vmess", host, port, source)
        except Exception:
            # Битый JSON или base64 — пропускаем
            continue


def _parse_json_config(text: str, source: str, out: list[ProxyCandidate], seen: set) -> None:
    """
    Парсинг JSON-конфигов Shadowsocks: {"server": "host", "server_port": port}
    Ищем через регулярку, не парся весь JSON.
    """
    for m in JSON_SERVER_RE.finditer(text):
        try:
            host = m.group("host").strip()
            port = int(m.group("port"))
            if host and port > 0:
                _add_candidate(out, seen, "ss", host, port, source)
        except Exception:
            continue


def _parse_space_format(
    text: str, source: str, default_scheme: str, out: list[ProxyCandidate], seen: set
) -> None:
    """
    Парсинг пробельно-табличного формата: 1.2.3.4 1080 / 1.2.3.4\t1080
    Тип прокси берётся из default_scheme.
    """
    for m in SPACE_RE.finditer(text):
        try:
            host = m.group("host")
            port = int(m.group("port"))
            _add_candidate(out, seen, default_scheme.lower(), host, port, source)
        except Exception:
            continue


def parse_candidates(text: str, source: str, default_scheme: str = "http") -> list[ProxyCandidate]:
    """
    Извлекает прокси-кандидатов из сырого текста.

    Порядок парсинга:
      1. Стандартный protocol://[user:pass@]host:port
      2. Shadowsocks URI (ss://base64...)
      3. JSON-конфиги ({"server": ..., "server_port": ...})
      4. VMess URI (vmess://base64...)
      5. Пробельно-табличный формат (ip port / ip\tport)
      6. Fallback: голый ip:port

    Дедупликация по ключу (proxy_type, host, port).
    """
    out: list[ProxyCandidate] = []
    seen: set = set()

    # 1. Стандартный формат protocol://[user:pass@]host:port
    for m in PROXY_RE.finditer(text):
        proxy_type = m.group("scheme").lower()
        host = m.group("host")
        port = int(m.group("port"))
        _add_candidate(out, seen, proxy_type, host, port, source)

    # 2. Shadowsocks URI (ss://base64...)
    _parse_ss_uri(text, source, out, seen)

    # 3. JSON-конфиги Shadowsocks
    _parse_json_config(text, source, out, seen)

    # 4. VMess URI (vmess://base64...)
    _parse_vmess_uri(text, source, out, seen)

    # 5. Пробельно-табличный формат
    _parse_space_format(text, source, default_scheme, out, seen)

    # 6. Fallback: голый ip:port (без схемы)
    for m in FALLBACK_RE.finditer(text):
        host = m.group("host")
        port = int(m.group("port"))
        _add_candidate(out, seen, default_scheme.lower(), host, port, source)

    return out
