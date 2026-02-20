from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass

import ssl

import httpx
from httpx_socks import AsyncProxyTransport

from app.normalizer import ProxyCandidate

logger = logging.getLogger(__name__)

# Pre-create SSL context to prevent event loop blocking on high concurrency
# ssl.create_default_context() does synchronous I/O which freezes the bot
_SHARED_SSL_CONTEXT = ssl.create_default_context()

CHECK_URLS = [
    "https://httpbin.org/ip",
    "https://api.ipify.org",
    "https://icanhazip.com",
    "https://checkip.amazonaws.com",
]

@dataclass(slots=True)
class ValidationResult:
    candidate: ProxyCandidate
    is_alive: bool
    latency_ms: float | None

async def _check_http(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """Проверка HTTP/HTTPS прокси через стандартный httpx."""
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    t0 = time.perf_counter()
    
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=timeout, follow_redirects=True, verify=_SHARED_SSL_CONTEXT) as client:
            async def _check_single_url(url: str) -> bool:
                try:
                    r = await client.get(url)
                    return r.status_code < 500
                except Exception:
                    return False
            
            tasks = [_check_single_url(url) for url in CHECK_URLS]
            for coro in asyncio.as_completed(tasks):
                is_ok = await coro
                if is_ok:
                    dt = (time.perf_counter() - t0) * 1000
                    return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
                    
            return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

async def _check_socks(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """Проверка SOCKS4/SOCKS5 прокси через httpx-socks AsyncProxyTransport."""
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    # Передаем verify, чтобы не грузить сертификаты заново каждый раз
    transport = AsyncProxyTransport.from_url(proxy_url, verify=_SHARED_SSL_CONTEXT)
    t0 = time.perf_counter()
    
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=True, verify=_SHARED_SSL_CONTEXT) as client:
            async def _check_single_url(url: str) -> bool:
                try:
                    r = await client.get(url)
                    return r.status_code < 500
                except Exception:
                    return False
            
            tasks = [_check_single_url(url) for url in CHECK_URLS]
            for coro in asyncio.as_completed(tasks):
                is_ok = await coro
                if is_ok:
                    dt = (time.perf_counter() - t0) * 1000
                    return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
                    
            return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

async def _check_tcp_only(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """Проверка TCP-подключения для MTProto и Shadowsocks."""
    t0 = time.perf_counter()
    try:
        conn = asyncio.open_connection(candidate.host, candidate.port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        dt = (time.perf_counter() - t0) * 1000
        
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
            
        return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

async def _check(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """Маршрутизатор стратегии проверки в зависимости от типа прокси."""
    pt = candidate.proxy_type.lower()
    if pt in ("http", "https"):
        return await _check_http(candidate, timeout)
    elif pt in ("socks4", "socks5"):
        return await _check_socks(candidate, timeout)
    elif pt in ("mtproto", "ss"):
        return await _check_tcp_only(candidate, timeout)
    else:
        return await _check_http(candidate, timeout)

async def validate_many(
    candidates: list[ProxyCandidate], timeout_sec: float, max_concurrent: int
) -> list[ValidationResult]:
    """Массовая асинхронная проверка списка кандидатов с ограничением конкурентности."""
    # Ограничиваем конкаренси жестче, чтобы не вешать луп
    sem = asyncio.Semaphore(min(max_concurrent, 100))

    async def run_one(c: ProxyCandidate) -> ValidationResult:
        async with sem:
            # Обязательно отдаем управление лупу, чтобы бот (Telegram) мог ответить на /stats
            await asyncio.sleep(0)
            return await _check(c, timeout_sec)

    results = await asyncio.gather(*(run_one(c) for c in candidates))
    
    alive_count = sum(1 for r in results if r.is_alive)
    latencies = [r.latency_ms for r in results if r.is_alive and r.latency_ms is not None]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    
    logger.info(
        "Валидация %d кандидатов завершена. Живых: %d, Мертвых: %d. Средний ping: %.1fms",
        len(candidates), alive_count, len(candidates) - alive_count, avg_latency
    )
    
    return results
