from __future__ import annotations

import asyncio
import base64
import re
import httpx
import logging

from app.collectors.base import Collector

logger = logging.getLogger(__name__)


TEXT_EXT_RE = re.compile(r"\.(txt|conf|cfg|ini|yaml|yml|json|csv|list|md)$", re.IGNORECASE)


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
        max_repos_to_scan: int | None = None,
    ) -> None:
        self.token = token
        self.queries = queries
        self.code_pages = code_pages
        self.repo_pages = repo_pages
        self.per_page = min(max(per_page, 1), 100)
        self.max_blob_bytes = max_blob_bytes
        self.extra_repos = extra_repos or []
        self.max_repos_to_scan = max_repos_to_scan

    async def collect(self) -> list[tuple[str, str]]:
        if not self.token:
            logger.warning("No GitHub token provided. GitHub code collection disabled.")
            return []

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        out: list[tuple[str, str]] = []
        seen_sources: set[str] = set()
        semaphore = asyncio.Semaphore(3)

        async def _fetch_code_item(client: httpx.AsyncClient, item: dict) -> None:
            source = item.get("html_url", "")
            if not source or source in seen_sources:
                return

            raw_url = source.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
            await asyncio.sleep(0.5)
            async with semaphore:
                try:
                    file_resp = await client.get(raw_url)
                except httpx.RequestError:
                    return

            if file_resp.status_code != 200:
                return
            
            content = file_resp.text
            if not content:
                return
            
            seen_sources.add(source)
            out.append((source, content))

        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            # 1) Deep code-search pagination by keywords.
            for q in self.queries:
                logger.info("Searching GitHub code for query: %s", q)
                for page in range(1, self.code_pages + 1):
                    search = await client.get(
                        "https://api.github.com/search/code",
                        params={"q": f"{q} in:file", "per_page": self.per_page, "page": page},
                    )
                    if search.status_code != 200:
                        logger.warning("GitHub code search failed or rate-limited: status %d", search.status_code)
                        break
                    items = search.json().get("items", [])
                    if not items:
                        break
                    
                    tasks = [_fetch_code_item(client, item) for item in items]
                    await asyncio.gather(*tasks)

            # 2) Repo discovery by keywords + README parsing for breadth.
            discovered_repos: set[str] = set(self.extra_repos)
            for q in self.queries:
                logger.info("Searching GitHub repositories for query: %s", q)
                for page in range(1, self.repo_pages + 1):
                    resp = await client.get(
                        "https://api.github.com/search/repositories",
                        params={
                            "q": q,
                            "sort": "updated",
                            "order": "desc",
                            "per_page": self.per_page,
                            "page": page,
                        },
                    )
                    if resp.status_code != 200:
                        logger.warning("GitHub repo search failed or rate-limited: status %d", resp.status_code)
                        break
                    items = resp.json().get("items", [])
                    if not items:
                        break
                    discovered_repos.update(item.get("full_name", "").lower() for item in items if item.get("full_name"))

            repo_list = list(discovered_repos)
            if self.max_repos_to_scan and len(repo_list) > self.max_repos_to_scan:
                repo_list = repo_list[:self.max_repos_to_scan]

            logger.info("Discovered %d repos to scan. Scanning trees...", len(repo_list))
            # 3) Focused deep scan of discovered repos via git tree and selected blobs.
            for repo in repo_list:
                await self._collect_repo_content(client, repo, out, seen_sources, semaphore)

        logger.info("GitHub code collection finished. Total items: %d", len(out))
        return out

    async def _collect_repo_content(
        self,
        client: httpx.AsyncClient,
        repo: str,
        out: list[tuple[str, str]],
        seen_sources: set[str],
        semaphore: asyncio.Semaphore,
    ) -> None:
        async def _safe_api_get(url: str, params: dict | None = None) -> httpx.Response | None:
            """Helper to handle secondary rate limits (403) with a short backoff."""
            for attempt in range(2):
                await asyncio.sleep(0.5)
                async with semaphore:
                    try:
                        resp = await client.get(url, params=params)
                        if resp.status_code == 403 and attempt == 0:
                            # Likely hit a secondary limit, sleep longer and retry
                            logger.warning("Hit 403 on %s, backing off for 2s...", url)
                            await asyncio.sleep(2.0)
                            continue
                        return resp
                    except httpx.RequestError:
                        return None
            return None

        repo_meta = await _safe_api_get(f"https://api.github.com/repos/{repo}")
        if not repo_meta or repo_meta.status_code != 200:
            return
        default_branch = repo_meta.json().get("default_branch", "main")

        readme = await _safe_api_get(f"https://api.github.com/repos/{repo}/readme")
        if readme and readme.status_code == 200:
            content = readme.json().get("content", "")
            if content:
                try:
                    decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                    source = f"https://github.com/{repo}#readme"
                    if source not in seen_sources:
                        seen_sources.add(source)
                        out.append((source, decoded))
                except Exception:
                    pass

        tree = await _safe_api_get(f"https://api.github.com/repos/{repo}/git/trees/{default_branch}", params={"recursive": 1})
        if not tree or tree.status_code != 200:
            return

        entries = tree.json().get("tree", [])
        candidates = [
            e for e in entries
            if e.get("type") == "blob"
            and e.get("size", 0) <= self.max_blob_bytes
            and (TEXT_EXT_RE.search(e.get("path", "")) or "proxy" in e.get("path", "").lower())
        ]

        async def _fetch_blob(blob: dict) -> None:
            path = blob.get("path", "")
            if not path:
                return
            
            raw_url = f"https://raw.githubusercontent.com/{repo}/{default_branch}/{path}"
            await asyncio.sleep(0.5)
            async with semaphore:
                try:
                    blob_resp = await client.get(raw_url)
                except httpx.RequestError:
                    return

            if blob_resp.status_code != 200:
                return
            
            content = blob_resp.text
            if not content:
                return
            
            source = f"https://github.com/{repo}/blob/{default_branch}/{path}"
            if source in seen_sources:
                return
            seen_sources.add(source)
            out.append((source, content))

        tasks = [_fetch_blob(blob) for blob in candidates[:200]]
        if tasks:
            await asyncio.gather(*tasks)
