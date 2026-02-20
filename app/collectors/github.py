from __future__ import annotations

import asyncio
import base64
import logging
import re
import time

import httpx

from app.collectors.base import Collector

logger = logging.getLogger(__name__)

TEXT_EXT_RE = re.compile(r"\.(txt|conf|cfg|ini|yaml|yml|json|csv|list|md)$", re.IGNORECASE)

# Максимальное время ожидания сброса rate limit (секунды)
_MAX_RATE_LIMIT_WAIT = 300

# Семафор для ограничения параллельных запросов к GitHub API (не более 3 одновременно)
_GITHUB_SEMAPHORE = asyncio.Semaphore(3)


async def _rate_limited_get(
    client: httpx.AsyncClient, url: str, max_retries: int = 3, **kwargs
) -> httpx.Response | None:
    """
    Обёртка над client.get() с обработкой rate limit, retry и backoff.

    Стратегия:
      - 200: вернуть Response
      - 403 + X-RateLimit-Remaining == 0: подождать до сброса лимита, retry
      - 403 по другой причине: вернуть None
      - 5xx: exponential backoff, retry
      - Сетевые ошибки (таймаут, коннект): exponential backoff, retry
      - 404/422: вернуть None
    """
    for attempt in range(max_retries):
        try:
            resp = await client.get(url, **kwargs)

            if resp.status_code == 200:
                return resp

            if resp.status_code == 403:
                # Проверяем, исчерпан ли rate limit
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining is not None and int(remaining) == 0:
                    # Вычисляем время до сброса лимита
                    reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
                    now = int(time.time())
                    wait = max(reset_ts - now, 1)
                    wait = min(wait, _MAX_RATE_LIMIT_WAIT)
                    logger.warning(
                        "GitHub rate limit исчерпан для %s. "
                        "Ожидание %d сек до сброса (попытка %d/%d).",
                        url, wait, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                else:
                    # 403 по другой причине (запрещённый ресурс и т.д.)
                    logger.warning("403 Forbidden (не rate limit) для %s", url)
                    return None

            if resp.status_code in (404, 422):
                # Ресурс не найден или невалидный запрос — не retry
                return None

            if resp.status_code >= 500:
                # Серверная ошибка — exponential backoff
                wait = 2 ** attempt
                logger.warning(
                    "GitHub вернул %d для %s. Backoff %d сек (попытка %d/%d).",
                    resp.status_code, url, wait, attempt + 1, max_retries,
                )
                await asyncio.sleep(wait)
                continue

            # Прочие статусы (4xx) — вернуть None, не retry
            return None

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
            # Сетевые ошибки — exponential backoff
            wait = 2 ** attempt
            logger.warning(
                "Сетевая ошибка %s для %s. Backoff %d сек (попытка %d/%d).",
                type(exc).__name__, url, wait, attempt + 1, max_retries,
            )
            await asyncio.sleep(wait)
            continue

    # Все попытки исчерпаны
    logger.error("Все %d попыток исчерпаны для %s", max_retries, url)
    return None


async def _search_keyword(
    client: httpx.AsyncClient,
    keyword: str,
    search_type: str,
    pages: int,
    per_page: int,
) -> list[dict]:
    """
    Поиск по одному ключевому слову через GitHub Search API.

    Параметры:
        search_type: "code" или "repositories"
        pages: количество страниц для пагинации

    Возвращает список items из всех страниц.
    Запросы ограничены через _GITHUB_SEMAPHORE.
    """
    all_items: list[dict] = []

    for page in range(1, pages + 1):
        async with _GITHUB_SEMAPHORE:
            if search_type == "code":
                params = {"q": f"{keyword} in:file", "per_page": per_page, "page": page}
            else:
                params = {
                    "q": keyword,
                    "sort": "updated",
                    "order": "desc",
                    "per_page": per_page,
                    "page": page,
                }

            resp = await _rate_limited_get(
                client,
                f"https://api.github.com/search/{search_type}",
                params=params,
            )

        if resp is None or resp.status_code != 200:
            break

        items = resp.json().get("items", [])
        if not items:
            break

        all_items.extend(items)

    return all_items


async def _fetch_file(
    client: httpx.AsyncClient, url: str
) -> tuple[str, str] | None:
    """
    Загрузка и декодирование одного файла по его API URL.
    Возвращает (html_url, decoded_content) или None при ошибке.
    Запрос ограничен через _GITHUB_SEMAPHORE.
    """
    async with _GITHUB_SEMAPHORE:
        file_resp = await _rate_limited_get(client, url)

    if file_resp is None or file_resp.status_code != 200:
        return None

    content = file_resp.json().get("content", "")
    if not content:
        return None

    try:
        decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
    except Exception:
        return None

    return url, decoded


async def _fetch_blob(
    client: httpx.AsyncClient,
    repo: str,
    sha: str,
    path: str,
    default_branch: str,
) -> tuple[str, str] | None:
    """
    Загрузка и декодирование одного blob-а по SHA.
    Возвращает (source_url, decoded_content) или None при ошибке.
    """
    async with _GITHUB_SEMAPHORE:
        blob_resp = await _rate_limited_get(
            client, f"https://api.github.com/repos/{repo}/git/blobs/{sha}"
        )

    if blob_resp is None or blob_resp.status_code != 200:
        return None

    payload = blob_resp.json()
    if payload.get("encoding") != "base64":
        return None

    try:
        decoded = base64.b64decode(payload.get("content", "")).decode("utf-8", errors="ignore")
    except Exception:
        return None

    source = f"https://github.com/{repo}/blob/{default_branch}/{path}"
    return source, decoded


class GitHubCodeCollector(Collector):
    def __init__(
        self,
        token: str,
        queries: list[str],
        code_pages: int = 5,
        repo_pages: int = 5,
        per_page: int = 50,
        max_blob_bytes: int = 250_000,
        extra_repos: list[str] | None = None,
    ) -> None:
        self.token = token
        self.queries = queries
        self.code_pages = code_pages
        self.repo_pages = repo_pages
        self.per_page = min(max(per_page, 1), 100)
        self.max_blob_bytes = max_blob_bytes
        self.extra_repos = extra_repos or []

    async def collect(self) -> list[tuple[str, str]]:
        if not self.token:
            return []

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        out: list[tuple[str, str]] = []
        seen_sources: set[str] = set()

        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            # --- Фаза 1: Параллельный поиск по всем ключевым словам ---
            # Создаём задачи на поиск кода и репозиториев для каждого keyword
            code_tasks = [
                _search_keyword(client, q, "code", self.code_pages, self.per_page)
                for q in self.queries
            ]
            repo_tasks = [
                _search_keyword(client, q, "repositories", self.repo_pages, self.per_page)
                for q in self.queries
            ]

            # Запускаем все поиски параллельно
            all_results = await asyncio.gather(
                *code_tasks, *repo_tasks, return_exceptions=True,
            )

            # Разделяем результаты: первые N — code, остальные — repos
            num_code = len(code_tasks)
            code_results = all_results[:num_code]
            repo_results = all_results[num_code:]

            # --- Фаза 2: Параллельная загрузка файлов из результатов code search ---
            file_tasks = []
            file_sources = []  # Соответствие task_index -> source для дедупликации

            for result in code_results:
                if isinstance(result, Exception):
                    logger.warning("Ошибка при поиске кода: %s", result)
                    continue
                for item in result:
                    url = item.get("url")
                    source = item.get("html_url", "github")
                    if not url or source in seen_sources:
                        continue
                    seen_sources.add(source)
                    file_tasks.append(_fetch_file(client, url))
                    file_sources.append(source)

            if file_tasks:
                file_results = await asyncio.gather(*file_tasks, return_exceptions=True)
                for i, fr in enumerate(file_results):
                    if isinstance(fr, Exception):
                        logger.warning("Ошибка загрузки файла: %s", fr)
                        continue
                    if fr is not None:
                        # fr = (api_url, decoded_content), заменяем url на html_url source
                        out.append((file_sources[i], fr[1]))

            # --- Фаза 3: Собираем обнаруженные репозитории ---
            discovered_repos: set[str] = set(self.extra_repos)
            for result in repo_results:
                if isinstance(result, Exception):
                    logger.warning("Ошибка при поиске репозиториев: %s", result)
                    continue
                discovered_repos.update(
                    item.get("full_name", "").lower()
                    for item in result
                    if item.get("full_name")
                )

            # --- Фаза 4: Параллельное глубокое сканирование репозиториев ---
            repo_scan_tasks = [
                self._collect_repo_content(client, repo, seen_sources)
                for repo in discovered_repos
            ]
            if repo_scan_tasks:
                repo_scan_results = await asyncio.gather(
                    *repo_scan_tasks, return_exceptions=True,
                )
                for result in repo_scan_results:
                    if isinstance(result, Exception):
                        logger.warning("Ошибка сканирования репозитория: %s", result)
                        continue
                    # result — это list[tuple[str, str]] от каждого репозитория
                    out.extend(result)

        return out

    async def _collect_repo_content(
        self,
        client: httpx.AsyncClient,
        repo: str,
        seen_sources: set[str],
    ) -> list[tuple[str, str]]:
        """
        Глубокое сканирование одного репозитория: README + tree + blobs.
        Возвращает список (source, content) для найденных файлов.
        """
        results: list[tuple[str, str]] = []

        # Получаем метаданные репозитория
        async with _GITHUB_SEMAPHORE:
            repo_meta = await _rate_limited_get(client, f"https://api.github.com/repos/{repo}")
        if repo_meta is None or repo_meta.status_code != 200:
            return results
        default_branch = repo_meta.json().get("default_branch", "main")

        # Пытаемся прочитать README
        async with _GITHUB_SEMAPHORE:
            readme = await _rate_limited_get(client, f"https://api.github.com/repos/{repo}/readme")
        if readme is not None and readme.status_code == 200:
            content = readme.json().get("content", "")
            if content:
                try:
                    decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                    source = f"https://github.com/{repo}#readme"
                    if source not in seen_sources:
                        seen_sources.add(source)
                        results.append((source, decoded))
                except Exception:
                    pass

        # Получаем дерево файлов репозитория
        async with _GITHUB_SEMAPHORE:
            tree = await _rate_limited_get(
                client,
                f"https://api.github.com/repos/{repo}/git/trees/{default_branch}",
                params={"recursive": 1},
            )
        if tree is None or tree.status_code != 200:
            return results

        entries = tree.json().get("tree", [])
        candidates = [
            e for e in entries
            if e.get("type") == "blob"
            and e.get("size", 0) <= self.max_blob_bytes
            and (TEXT_EXT_RE.search(e.get("path", "")) or "proxy" in e.get("path", "").lower())
        ]

        # Параллельная загрузка blob-ов (ограничение до 200 файлов на репозиторий)
        blob_tasks = []
        for blob_entry in candidates[:200]:
            sha = blob_entry.get("sha")
            path = blob_entry.get("path", "")
            if not sha:
                continue
            blob_tasks.append(_fetch_blob(client, repo, sha, path, default_branch))

        if blob_tasks:
            blob_results = await asyncio.gather(*blob_tasks, return_exceptions=True)
            for br in blob_results:
                if isinstance(br, Exception):
                    logger.warning("Ошибка загрузки blob в %s: %s", repo, br)
                    continue
                if br is not None:
                    source, decoded = br
                    if source not in seen_sources:
                        seen_sources.add(source)
                        results.append((source, decoded))

        return results
