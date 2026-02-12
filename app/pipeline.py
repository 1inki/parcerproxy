from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections import defaultdict

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


def _stage(title: str) -> str:
    return f"\033[1m{title}\033[0m"


def _prepare_candidates(candidates: list[ProxyCandidate], limit: int) -> list[ProxyCandidate]:
    seen: set[tuple[str, str, int]] = set()
    dedup: list[ProxyCandidate] = []
    for c in candidates:
        key = (c.proxy_type, c.host, c.port)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(c)

    by_type: dict[str, list[ProxyCandidate]] = defaultdict(list)
    for c in dedup:
        by_type[c.proxy_type].append(c)

    ordered_types = ["socks5", "mtproto", "ss", "http", "https", "socks4"]
    out: list[ProxyCandidate] = []
    progress = True
    i = 0
    while progress and len(out) < limit:
        progress = False
        for t in ordered_types:
            arr = by_type.get(t, [])
            if i < len(arr):
                out.append(arr[i])
                progress = True
                if len(out) >= limit:
                    break
        i += 1

    if len(out) < limit:
        leftovers = [c for t, arr in by_type.items() if t not in ordered_types for c in arr]
        out.extend(leftovers[: max(0, limit - len(out))])

    return out


class Pipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = Storage(settings.db_url)
        self.storage.init_db()

    async def run_once(self, test_mode: bool = False) -> dict[str, int]:
        logger.info("%s cycle started (test_mode=%s)", _stage("ПАРСИНГ:"), test_mode)
        queued_repos = self.storage.get_pending_repos(limit=100)
        logger.info("Queued repos fetched: %s", len(queued_repos))
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
                max_files_per_query=self.settings.github_max_files_per_query,
                test_mode=test_mode,
                test_cycle_repos=self.settings.github_test_cycle_repos,
            ),
            URLListCollector(self.settings.source_urls),
        ]

        raws: list[tuple[str, str]] = []
        collect_error = False
        for collector in collectors:
            try:
                logger.info("Running collector: %s", collector.__class__.__name__)
                raws.extend(await collector.collect())
            except asyncio.CancelledError:
                collect_error = True
                logger.warning("Collector cancelled: %s", collector.__class__.__name__)
            except Exception:
                collect_error = True
                logger.exception("Collector failed: %s", collector.__class__.__name__)

        logger.info("Raw documents collected: %s", len(raws))

        logger.info("%s normalize and dedup candidates", _stage("СОРТИРОВКА:"))
        candidates: list[ProxyCandidate] = []
        for source, text in raws:
            candidates.extend(parse_candidates(text, source=source))

        pre_count = len(candidates)
        limit = self.settings.max_validate_candidates_test if test_mode else self.settings.max_validate_candidates
        candidates = _prepare_candidates(candidates, limit=limit)

        logger.info("Candidates parsed: %s | prepared for validation: %s", pre_count, len(candidates))

        logger.info("%s async validate", _stage("ПРОВЕРКА:"))
        validated = await validate_many(candidates, self.settings.check_timeout_sec, self.settings.max_concurrent_checks)
        logger.info("Candidates validated: %s", len(validated))

        logger.info("%s saving to DB + geo filters", _stage("СОХРАНЕНИЕ:"))
        saved = 0
        alive = 0
        for item in validated:
            country = None
            if _is_ip(item.candidate.host):
                country = await country_by_ip(item.candidate.host)

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

        final_status = "failed" if collect_error else "done"
        for repo in queued_repos:
            self.storage.mark_repo_status(repo, final_status)

        stats = {
            "raw_sources": len(raws),
            "candidates": pre_count,
            "validated": len(candidates),
            "saved": saved,
            "alive": alive,
        }
        self.storage.record_run(raw_sources=stats["raw_sources"], candidates=stats["candidates"], saved=stats["saved"], alive=stats["alive"])
        logger.info("%s cycle finished: %s", _stage("ИТОГ:"), stats)
        return stats


def run_once_sync(settings: Settings, test_mode: bool = False) -> dict[str, int]:
    return asyncio.run(Pipeline(settings).run_once(test_mode=test_mode))
