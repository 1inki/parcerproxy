from __future__ import annotations

import httpx
import ipaddress

_geo_cache: dict[str, str | None] = {}

async def country_by_ip(ip: str) -> str | None:
    if ip in _geo_cache:
        return _geo_cache[ip]

    try:
        # Быстрая проверка на локальные адреса, чтобы не спамить API
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback:
            _geo_cache[ip] = None
            return None
    except ValueError:
        pass

    # Публичный endpoint, best-effort. Для продакшена лучше локальный GeoIP DB.
    url = f"https://ipapi.co/{ip}/country/"
    try:
        async with httpx.AsyncClient(timeout=3, verify=False) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                _geo_cache[ip] = None
                return None
            cc = resp.text.strip().upper()
            result = cc if len(cc) == 2 else None
            _geo_cache[ip] = result
            return result
    except Exception:
        _geo_cache[ip] = None
        return None
