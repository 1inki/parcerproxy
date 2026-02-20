from __future__ import annotations

import asyncio
import time
import logging
from dataclasses import dataclass

from app.normalizer import ProxyCandidate

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ValidationResult:
    candidate: ProxyCandidate
    is_alive: bool
    latency_ms: float | None


import requests
from concurrent.futures import ThreadPoolExecutor

def _check_sync(candidate: ProxyCandidate, timeout_sec: float) -> ValidationResult:
    proxy_url = f"{candidate.proxy_type}://{candidate.host}:{candidate.port}"
    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }
    
    for attempt in range(2):
        t0 = time.perf_counter()
        try:
            # Using requests inside a thread completely bypasses asyncio OS-level socket bugs on Windows
            r = requests.get("http://1.1.1.1/cdn-cgi/trace", proxies=proxies, timeout=timeout_sec, verify=False)
            if r.status_code < 400:
                dt = (time.perf_counter() - t0) * 1000
                return ValidationResult(candidate=candidate, is_alive=True, latency_ms=dt)
        except Exception:
            pass
        if attempt == 0:
            time.sleep(0.5)

    return ValidationResult(candidate=candidate, is_alive=False, latency_ms=None)


async def validate_many(candidates: list[ProxyCandidate], timeout_sec: float, max_concurrent: int) -> list[ValidationResult]:
    if not candidates:
        return []

    total = len(candidates)
    results: list[ValidationResult] = []
    done = 0

    # Ensure max_concurrent is reasonable for threads
    threads = min(max_concurrent, 200)

    loop = asyncio.get_running_loop()
    
    logger.info("Starting validation using ThreadPoolExecutor with %d threads", threads)

    # Use ThreadPoolExecutor to completely isolate blocking socket operations from asyncio loop
    with ThreadPoolExecutor(max_workers=threads) as executor:
        fs = [loop.run_in_executor(executor, _check_sync, c, timeout_sec) for c in candidates]
        
        # We await them as they complete to report progress without blocking
        for i, coro in enumerate(asyncio.as_completed(fs)):
            res = await coro
            results.append(res)
            
            done += 1
            if done % max(1, total // 10) == 0 or done == total:
                logger.info("Validation progress: %d/%d (%.1f%%) done", done, total, (done / total) * 100)
                await asyncio.sleep(0.01) # Breathe
                
    return results
