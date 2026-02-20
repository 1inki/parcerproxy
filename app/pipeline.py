from __future__ import annotations

import asyncio
import ipaddress
import logging

from app.collectors.github import GitHubCodeCollector
from app.collectors.url_list import URLListCollector
from app.config import Settings
from app.geo import country_by_ip
from app.normalizer import ProxyCandidate, parse_candidates
from app.storage import Storage
from app.validator import validate_many

logger = logging.getLogger(__name__)


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

    async def run_once(self, test_mode: bool = False) -> dict[str, int]:
        logger.info("Starting Pipeline run_once cycle (test_mode=%s)", test_mode)
        queued_repos = self.storage.get_pending_repos(limit=2 if test_mode else 100)
        logger.info("Found %d pending repos in queue", len(queued_repos))
        
        for repo in queued_repos:
            self.storage.mark_repo_status(repo, "processing")
            
        collectors = [
            GitHubCodeCollector(
                token=self.settings.github_token,
                queries=self.settings.github_queries,
                code_pages=1 if test_mode else self.settings.github_code_pages,
                repo_pages=1 if test_mode else self.settings.github_repo_pages,
                per_page=self.settings.github_per_page,
                max_blob_bytes=self.settings.github_max_blob_bytes,
                extra_repos=queued_repos,
                max_repos_to_scan=2 if test_mode else None,
            ),
            URLListCollector(self.settings.source_urls),
        ]

        raws: list[tuple[str, str]] = []
        for collector in collectors:
            logger.info("Running collector: %s", collector.__class__.__name__)
            collected = await collector.collect()
            logger.info("%s yielded %d raw items", collector.__class__.__name__, len(collected))
            raws.extend(collected)

        # Offload CPU-bound parsing to a separate thread
        def parse_all() -> list[ProxyCandidate]:
            out = []
            for source, text in raws:
                out.extend(parse_candidates(text, source=source))
            return out
        
        candidates = await asyncio.to_thread(parse_all)
        
        if test_mode and len(candidates) > 7000:
            logger.info("Test mode: limiting candidates from %d to 7000", len(candidates))
            candidates = candidates[:7000]
            
        logger.info("Parsed %d proxy candidates from all sources", len(candidates))

        logger.info("Starting validation of candidates...")
        validated = await validate_many(candidates, self.settings.check_timeout_sec, self.settings.max_concurrent_checks)
        logger.info("Validation finished. Checking GeoIP for alive proxies...")

        saved = 0
        alive = 0
        for item in validated:
            country = None
            if item.is_alive and _is_ip(item.candidate.host):
                country = await country_by_ip(item.candidate.host)

            if self.settings.country_whitelist and country and country not in self.settings.country_whitelist:
                continue
                
            dynamic_blacklist = self.storage.get_country_blacklist(self.settings.country_blacklist)
            if country and country in dynamic_blacklist:
                continue

            await asyncio.to_thread(
                self.storage.upsert_proxy,
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
            await asyncio.to_thread(self.storage.mark_repo_status, repo, "done")

        stats = {
            "raw_sources": len(raws),
            "candidates": len(candidates),
            "saved": saved,
            "alive": alive,
        }
        await asyncio.to_thread(self.storage.record_run, **stats)
        return stats


def run_once_sync(settings: Settings, test_mode: bool = False) -> dict[str, int]:
    return asyncio.run(Pipeline(settings).run_once(test_mode=test_mode))
