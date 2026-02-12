from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.normalizer import ProxyCandidate


@dataclass(slots=True)
class ValidationResult:
    candidate: ProxyCandidate
    is_alive: bool
    latency_ms: float | None


async def _check(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:
    # Универсальная быстрая проверка TCP-like доступности через HTTP endpoint.
    # Для socks/mtproto/ss это индикативно, не полная функциональная проверка протокола.
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout_sec, follow_redirects=True) as client:
            r = await client.get("https://httpbin.org/ip")
            ok = r.status_code < 500
            dt = (time.perf_counter() - t0) * 1000
            return ValidationResult(candidate=candidate, is_alive=ok, latency_ms=dt)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def validate_many(candidates: list[ProxyCandidate], timeout_sec: float, max_concurrent: int) -> list[ValidationResult]:
    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(c: ProxyCandidate) -> ValidationResult:
        async with sem:
            return await _check(c, timeout_sec)

    return await asyncio.gather(*(run_one(c) for c in candidates))
