from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

from app.collectors.github import GitHubCodeCollector

logger = logging.getLogger(__name__)
from app.collectors.url_list import URLListCollector
from app.config import Settings
from app.geo import country_by_ip
from app.normalizer import ProxyCandidate, parse_candidates
from app.storage import Storage
from app.validator import validate_many


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


class Pipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_url)
        self.storage.init_db()

    async def run_once(self, test_mode: bool = False, fast_test: bool = False) -> dict[str, int]:
        t_start = time.perf_counter()
        mode_str = "(FAST TEST)" if fast_test else ("(TEST MODE)" if test_mode else "")
        logger.info(f"=== Запуск цикла парсинга прокси {mode_str} ===")
        
        queued_repos = await asyncio.to_thread(self.storage.get_pending_repos, 100)
        if fast_test:
            queued_repos = queued_repos[:1]
        elif test_mode:
            queued_repos = queued_repos[:5]
            
        if queued_repos:
            logger.info("Взято %d репозиториев из очереди", len(queued_repos))
        
        for repo in queued_repos:
            await asyncio.to_thread(self.storage.mark_repo_status, repo, "processing")

        collectors = []
        if not fast_test:
            collectors.append(
                GitHubCodeCollector(
                    token=self.settings.github_token,
                    queries=self.settings.github_queries[:1] if test_mode else self.settings.github_queries,
                    code_pages=1 if test_mode else self.settings.github_code_pages,
                    repo_pages=1 if test_mode else self.settings.github_repo_pages,
                    per_page=3 if test_mode else self.settings.github_per_page,
                    max_blob_bytes=self.settings.github_max_blob_bytes,
                    extra_repos=queued_repos,
                )
            )
        
        # Если это fast_test и кастомных урлов нет, добавим один для гарантии получения прокси
        test_urls = self.settings.source_urls[:2] if self.settings.source_urls else ["https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"]
        
        collectors.append(URLListCollector(test_urls if fast_test else self.settings.source_urls))

        logger.info("Запуск коллекторов...")
        collector_results = await asyncio.gather(*(c.collect() for c in collectors))
        raws: list[tuple[str, str]] = []
        for result in collector_results:
            raws.extend(result)
            
        if fast_test:
            raws = raws[:3]
            
        logger.info("Собрано %d сырых текстов. Начинаю извлечение прокси...", len(raws))

        candidates: list[ProxyCandidate] = []
        for source, text in raws:
            parsed = await asyncio.to_thread(parse_candidates, text, source)
            candidates.extend(parsed)
            
        logger.info("Извлечено %d кандидатов. Начинаю валидацию...", len(candidates))

        validated = await validate_many(candidates, self.settings.check_timeout_sec, self.settings.max_concurrent_checks)

        # --- Параллельное гео-определение ---
        # Собираем уникальные IP-адреса из валидированных кандидатов
        unique_ips = {
            item.candidate.host
            for item in validated
            if _is_ip(item.candidate.host)
        }

        logger.info("Определяем геолокацию для %d уникальных IP...", len(unique_ips))
        
        # Запускаем гео-запросы параллельно для всех уникальных IP в потоке
        ip_list = list(unique_ips)
        if ip_list:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                geo_results = await loop.run_in_executor(pool, lambda: list(pool.map(country_by_ip, ip_list)))
            ip_to_country: dict[str, str | None] = dict(zip(ip_list, geo_results))
        else:
            ip_to_country = {}

        # Обрабатываем результаты валидации с уже готовыми гео-данными
        saved = 0
        alive = 0
        # Подготавливаем данные для пакетной вставки/обновления
        items_to_upsert = []
        for item in validated:
            country = ip_to_country.get(item.candidate.host)

            if self.settings.country_whitelist:
                if not country or country not in self.settings.country_whitelist:
                    continue
            
            if country and country in self.settings.country_blacklist:
                continue

            items_to_upsert.append({
                "proxy_type": item.candidate.proxy_type,
                "host": item.candidate.host,
                "port": item.candidate.port,
                "source": item.candidate.source,
                "country": country,
                "is_alive": item.is_alive,
                "latency_ms": item.latency_ms,
            })
            
            saved += 1
            if item.is_alive:
                alive += 1
                
        # Пакетное сохранение в БД за одну транзакцию в отдельном потоке
        if items_to_upsert:
            await asyncio.to_thread(self.storage.batch_upsert_proxies, items_to_upsert)

        for repo in queued_repos:
            await asyncio.to_thread(self.storage.mark_repo_status, repo, "done")

        stats = {
            "raw_sources": len(raws),
            "candidates": len(candidates),
            "saved": saved,
            "alive": alive,
        }
        await asyncio.to_thread(self.storage.record_run, **stats)
        
        dt = time.perf_counter() - t_start
        logger.info(
            "=== Цикл завершен за %.2f сек. Сырых: %d | Кандидатов: %d | Сохранено: %d | Живых: %d ===",
            dt, len(raws), len(candidates), saved, alive
        )
        return stats


def run_once_sync(settings: Settings, test_mode: bool = False, fast_test: bool = False) -> dict[str, int]:
    return asyncio.run(Pipeline(settings).run_once(test_mode=test_mode, fast_test=fast_test))
