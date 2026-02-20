from __future__ import annotations

import asyncio
import logging

import httpx

from app.collectors.base import Collector
from app.retry import retry_async

logger = logging.getLogger(__name__)


async def _fetch_one(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """
    Загрузка одного URL с retry.
    Возвращает (url, text) или None при ошибке.
    """
    try:
        r = await retry_async(client.get, url, max_attempts=3, base_delay=1.0)
        if r.status_code == 200 and r.text:
            return (url, r.text)
        return None
    except Exception as exc:
        # Все retry исчерпаны — логируем и возвращаем None
        logger.warning("Не удалось загрузить %s после всех попыток: %s", url, exc)
        return None


class URLListCollector(Collector):
    def __init__(self, urls: list[str]) -> None:
        self.urls = urls

    async def collect(self) -> list[tuple[str, str]]:
        if not self.urls:
            return []

        async with httpx.AsyncClient(timeout=10) as client:
            # Параллельная загрузка всех URL через asyncio.gather
            results = await asyncio.gather(
                *(_fetch_one(client, url) for url in self.urls)
            )

        # Отфильтровываем None (неудавшиеся загрузки)
        return [r for r in results if r is not None]
