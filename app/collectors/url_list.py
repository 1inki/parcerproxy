from __future__ import annotations

import httpx

from app.collectors.base import Collector


class URLListCollector(Collector):
    def __init__(self, urls: list[str]) -> None:
        self.urls = urls

    async def collect(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if not self.urls:
            return out

        async with httpx.AsyncClient(timeout=10) as client:
            for url in self.urls:
                r = await client.get(url)
                if r.status_code == 200 and r.text:
                    out.append((url, r.text))
        return out
