from __future__ import annotations

import httpx


async def country_by_ip(ip: str) -> str | None:
    # Публичный endpoint, best-effort. Для продакшена лучше локальный GeoIP DB.
    url = f"https://ipapi.co/{ip}/country/"
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            cc = resp.text.strip().upper()
            return cc if len(cc) == 2 else None
    except Exception:
        return None
