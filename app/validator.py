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


async def _check_tcp(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:
    t0 = time.perf_counter()
    try:
        conn = asyncio.open_connection(candidate.host, candidate.port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout_sec)
        writer.close()
        await writer.wait_closed()
        dt = (time.perf_counter() - t0) * 1000
        return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def _check_http_proxy(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    t0 = time.perf_counter()
    try:
        timeout = httpx.Timeout(connect=timeout_sec, read=timeout_sec, write=timeout_sec, pool=timeout_sec)
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout, follow_redirects=True) as client:
            r = await client.get("https://httpbin.org/ip")
            ok = r.status_code < 500
            dt = (time.perf_counter() - t0) * 1000
            return ValidationResult(candidate=candidate, is_alive=ok, latency_ms=dt)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def _check(candidate: ProxyCandidate, timeout_sec: float, mode: str) -> ValidationResult:
    mode = mode.lower()
    if mode == "http" and candidate.proxy_type in {"http", "https"}:
        return await _check_http_proxy(candidate, timeout_sec)
    return await _check_tcp(candidate, timeout_sec)


async def validate_many(
    candidates: list[ProxyCandidate],
    timeout_sec: float,
    max_concurrent: int,
    mode: str = "tcp",
) -> list[ValidationResult]:
    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(c: ProxyCandidate) -> ValidationResult:
        async with sem:
            return await _check(c, timeout_sec, mode=mode)

    return await asyncio.gather(*(run_one(c) for c in candidates))
