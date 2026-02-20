from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# In-memory кэш для геоданных: IP -> код страны (или None при неудаче)
_geo_cache: dict[str, str | None] = {}

# Количество попыток для запроса геолокации
_MAX_GEO_ATTEMPTS = 2


async def country_by_ip(ip: str) -> str | None:
    """
    Определяет страну по IP-адресу через публичный API ipapi.co.

    Использует in-memory кэш, чтобы не повторять запросы для уже известных IP.
    При получении 429 (rate limit) — ждёт и повторяет.
    При полном провале — кэширует None, чтобы не повторять безнадёжные запросы.
    """
    # Проверяем кэш — если IP уже запрашивался, возвращаем результат сразу
    if ip in _geo_cache:
        return _geo_cache[ip]

    url = f"https://ipapi.co/{ip}/country/"

    for attempt in range(_MAX_GEO_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(url)

                if resp.status_code == 200:
                    cc = resp.text.strip().upper()
                    result = cc if len(cc) == 2 else None
                    _geo_cache[ip] = result
                    return result

                if resp.status_code == 429:
                    # Rate limit — ждём перед повтором
                    wait = 2 ** attempt
                    logger.warning(
                        "Geo API rate limit (429) для IP %s. "
                        "Ожидание %d сек (попытка %d/%d).",
                        ip, wait, attempt + 1, _MAX_GEO_ATTEMPTS,
                    )
                    await asyncio.sleep(wait)
                    continue

                # Другие ошибочные статусы — не retry
                logger.debug("Geo API вернул %d для IP %s", resp.status_code, ip)
                _geo_cache[ip] = None
                return None

        except Exception as exc:
            if attempt < _MAX_GEO_ATTEMPTS - 1:
                wait = 2 ** attempt
                logger.warning(
                    "Ошибка geo-запроса для IP %s: %s. "
                    "Повтор через %d сек (попытка %d/%d).",
                    ip, exc, wait, attempt + 1, _MAX_GEO_ATTEMPTS,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "Все попытки geo-запроса для IP %s исчерпаны: %s",
                    ip, exc,
                )

    # Все попытки исчерпаны — кэшируем None, чтобы не повторять
    _geo_cache[ip] = None
    return None
