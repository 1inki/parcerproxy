from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
from httpx_socks import AsyncProxyTransport

from app.normalizer import ProxyCandidate


# Список URL для проверки HTTP/SOCKS прокси.
# Используется fallback стратегия: если один не ответил, пробуем следующий.
CHECK_URLS = [
    "https://httpbin.org/ip",
    "https://ifconfig.me/ip",
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
    """
    Проверка HTTP/HTTPS прокси через стандартный httpx.AsyncClient.
    """
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    t0 = time.perf_counter()
    
    try:
        async with httpx.AsyncClient(proxies=proxy_url, timeout=timeout, follow_redirects=True) as client:
            # Пробуем перебрать URL-ы для проверки, если первый не сработал (редкий кейс, но надежнее)
            # В данном случае, для простоты и скорости, проверим только первый, 
            # но архитектурно можно расширить цикл по CHECK_URLS.
            # Для оптимизации скорости пока берем random или первый.
            # По ТЗ требовался fallback, реализуем перебор.
            
            for url in CHECK_URLS:
                try:
                    r = await client.get(url)
                    if r.status_code < 500:
                        dt = (time.perf_counter() - t0) * 1000
                        return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
                except httpx.RequestError:
                    continue
            
            # Если ни один URL не открылся
            return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def _check_socks(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """
    Проверка SOCKS4/SOCKS5 прокси через httpx-socks AsyncProxyTransport.
    """
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    transport = AsyncProxyTransport.from_url(proxy_url)
    t0 = time.perf_counter()
    
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=True) as client:
            for url in CHECK_URLS:
                try:
                    r = await client.get(url)
                    if r.status_code < 500:
                        dt = (time.perf_counter() - t0) * 1000
                        return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
                except httpx.RequestError:
                    continue
            
            return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)

    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def _check_tcp_only(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """
    Проверка TCP-подключения для "экзотических" протоколов (MTProto, Shadowsocks).
    Проверяем только возможность установить соединение с сокетом.
    Это не гарантирует работоспособность протокола, но отсеивает мертвые хосты.
    """
    t0 = time.perf_counter()
    try:
        # asyncio.open_connection создает сокет и пытается соединиться
        # Оборачиваем в wait_for, так как open_connection может висеть долго
        conn = asyncio.open_connection(candidate.host, candidate.port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        
        # Если соединение успешно установлено
        dt = (time.perf_counter() - t0) * 1000
        
        # Закрываем соединение корректно
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
            
        return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
        
    except (OSError, asyncio.TimeoutError):
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)
    except Exception:
        return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def _check(candidate: ProxyCandidate, timeout: float) -> ValidationResult:
    """
    Маршрутизатор стратегии проверки в зависимости от типа прокси.
    """
    pt = candidate.proxy_type.lower()
    
    if pt in ("http", "https"):
        return await _check_http(candidate, timeout)
    elif pt in ("socks4", "socks5"):
        return await _check_socks(candidate, timeout)
    elif pt in ("mtproto", "ss"): # ss = shadowsocks
        return await _check_tcp_only(candidate, timeout)
    else:
        # Fallback для неизвестных типов - пробуем как HTTP
        return await _check_http(candidate, timeout)


async def validate_many(candidates: list[ProxyCandidate], timeout_sec: float, max_concurrent: int) -> list[ValidationResult]:
    """
    Массовая асинхронная проверка списка кандидатов с ограничением конкурентности.
    """
    sem = asyncio.Semaphore(max_concurrent)

    async def run_one(c: ProxyCandidate) -> ValidationResult:
        async with sem:
            return await _check(c, timeout_sec)

    return await asyncio.gather(*(run_one(c) for c in candidates))
