from __future__ import annotations

import asyncio
import ipaddress

from app.collectors.github import GitHubCodeCollector
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

    async def run_once(self) -> dict[str, int]:
        queued_repos = self.storage.get_pending_repos(limit=100)
        for repo in queued_repos:
            self.storage.mark_repo_status(repo, "processing")

        collectors = [
            GitHubCodeCollector(
                token=self.settings.github_token,
                queries=self.settings.github_queries,
                code_pages=self.settings.github_code_pages,
                repo_pages=self.settings.github_repo_pages,
                per_page=self.settings.github_per_page,
                max_blob_bytes=self.settings.github_max_blob_bytes,
                extra_repos=queued_repos,
            ),
            URLListCollector(self.settings.source_urls),
        ]

        # Параллельный запуск всех коллекторов через asyncio.gather
        collector_results = await asyncio.gather(*(c.collect() for c in collectors))
        raws: list[tuple[str, str]] = []
        for result in collector_results:
            raws.extend(result)

        candidates: list[ProxyCandidate] = []
        for source, text in raws:
            candidates.extend(parse_candidates(text, source=source))

        validated = await validate_many(candidates, self.settings.check_timeout_sec, self.settings.max_concurrent_checks)

        # --- Параллельное гео-определение ---
        # Собираем уникальные IP-адреса из валидированных кандидатов
        unique_ips = {
            item.candidate.host
            for item in validated
            if _is_ip(item.candidate.host)
        }

        # Запускаем гео-запросы параллельно для всех уникальных IP
        ip_list = list(unique_ips)
        if ip_list:
            geo_results = await asyncio.gather(*(country_by_ip(ip) for ip in ip_list))
            ip_to_country: dict[str, str | None] = dict(zip(ip_list, geo_results))
        else:
            ip_to_country = {}

        # Обрабатываем результаты валидации с уже готовыми гео-данными
        saved = 0
        alive = 0
        for item in validated:
            country = ip_to_country.get(item.candidate.host)

            if self.settings.country_whitelist and country and country not in self.settings.country_whitelist:
                continue
            if country and country in self.settings.country_blacklist:
                continue

            self.storage.upsert_proxy(
                proxy_type=item.candidate.proxy_type,
                host=item.candidate.host,
                port=item.candidate.port,
                source=item.candidate.source,
                country=country,
                is_alive=item.is_alive,
                latency_ms=item.latency_ms,
            )
            saved += 1
            if item.is_alive:
                alive += 1

        for repo in queued_repos:
            self.storage.mark_repo_status(repo, "done")

        stats = {
            "raw_sources": len(raws),
            "candidates": len(candidates),
            "saved": saved,
            "alive": alive,
        }
        self.storage.record_run(**stats)
        return stats


def run_once_sync(settings: Settings) -> dict[str, int]:
    return asyncio.run(Pipeline(settings).run_once())
