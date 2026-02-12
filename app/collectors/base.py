from __future__ import annotations

from abc import ABC, abstractmethod


class Collector(ABC):
    @abstractmethod
    async def collect(self) -> list[tuple[str, str]]:
        """Return list[(source_name, raw_text)]"""
