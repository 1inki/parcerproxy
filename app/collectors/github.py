from __future__ import annotations

import asyncio
import base64
import logging
import re

import httpx

from app.collectors.base import Collector


TEXT_EXT_RE = re.compile(r"\.(txt|conf|cfg|ini|yaml|yml|json|csv|list|md)$", re.IGNORECASE)
PROXYISH_PATH_RE = re.compile(r"(proxy|socks|mtproto|shadow|v2ray|trojan|vpn|ss)", re.IGNORECASE)
logger = logging.getLogger(__name__)


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
        max_files_per_query: int = 80,
        test_mode: bool = False,
        test_cycle_repos: int = 4,
    ) -> None:
        self.token = token
        self.queries = queries
        self.code_pages = code_pages
        self.repo_pages = repo_pages
        self.per_page = min(max(per_page, 1), 100)
        self.max_blob_bytes = max_blob_bytes
        self.extra_repos = extra_repos or []
        self.max_files_per_query = max(1, max_files_per_query)
        self.test_mode = test_mode
        self.test_cycle_repos = max(1, test_cycle_repos)

    async def _get_json(self, client: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                logger.debug("GitHub non-200: %s %s", resp.status_code, url)
                return None
            return resp.json()
        except asyncio.CancelledError:
            logger.warning("GitHub request cancelled, skipping: %s", url)
            return None
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            logger.warning("GitHub request failed: %s (%s)", url, exc)
            return None

    async def collect(self) -> list[tuple[str, str]]:
        if not self.token:
            logger.warning("GITHUB_TOKEN is empty, GitHub collector disabled")
            return []

        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        out: list[tuple[str, str]] = []
        seen_sources: set[str] = set()
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=20.0)

        logger.info("GitHub collector started: queries=%s test_mode=%s", len(self.queries), self.test_mode)
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            discovered_repos: set[str] = set(self.extra_repos)

            if self.test_mode:
                discovered_repos.update(await self._discover_protocol_test_repos(client))
            else:
                await self._collect_code_search_docs(client, out, seen_sources)
                discovered_repos.update(await self._discover_repos_by_queries(client))

            logger.info("GitHub repo deep scan count=%s", len(discovered_repos))
            for repo in discovered_repos:
                await self._collect_repo_content(client, repo, out, seen_sources)

        logger.info("GitHub collector finished: documents=%s", len(out))
        return out

    async def _collect_code_search_docs(
        self,
        client: httpx.AsyncClient,
        out: list[tuple[str, str]],
        seen_sources: set[str],
    ) -> None:
        for q in self.queries:
            logger.info("GitHub code search query=%s", q)
            accepted = 0
            for page in range(1, self.code_pages + 1):
                payload = await self._get_json(
                    client,
                    "https://api.github.com/search/code",
                    params={"q": f"{q} in:file", "per_page": self.per_page, "page": page},
                )
                if not payload:
                    break
                items = payload.get("items", [])
                if not items:
                    break
                for item in items:
                    path = item.get("path", "")
                    if path and not PROXYISH_PATH_RE.search(path):
                        continue
                    url = item.get("url")
                    source = item.get("html_url", "github")
                    if not url or source in seen_sources:
                        continue
                    file_payload = await self._get_json(client, url)
                    if not file_payload:
                        continue
                    content = file_payload.get("content", "")
                    if not content:
                        continue
                    try:
                        decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                    seen_sources.add(source)
                    out.append((source, decoded))
                    accepted += 1
                    if accepted >= self.max_files_per_query:
                        break
                if accepted >= self.max_files_per_query:
                    break

    async def _discover_repos_by_queries(self, client: httpx.AsyncClient) -> set[str]:
        discovered_repos: set[str] = set()
        for q in self.queries:
            for page in range(1, self.repo_pages + 1):
                payload = await self._get_json(
                    client,
                    "https://api.github.com/search/repositories",
                    params={
                        "q": f"{q} proxy socks mtproto shadowsocks in:readme,description",
                        "sort": "updated",
                        "order": "desc",
                        "per_page": self.per_page,
                        "page": page,
                    },
                )
                if not payload:
                    break
                items = payload.get("items", [])
                if not items:
                    break
                discovered_repos.update(item.get("full_name", "").lower() for item in items if item.get("full_name"))
        return discovered_repos

    async def _discover_protocol_test_repos(self, client: httpx.AsyncClient) -> set[str]:
        targets = [
            ("socks5", "socks5 proxy list"),
            ("mtproto", "mtproto proxy telegram"),
            ("ss", "shadowsocks ss proxy"),
            ("http", "http proxy list"),
        ]
        repos: set[str] = set()
        for proto, q in targets:
            payload = await self._get_json(
                client,
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "updated", "order": "desc", "per_page": 20, "page": 1},
            )
            if not payload:
                continue
            for item in payload.get("items", []):
                full = (item.get("full_name") or "").lower()
                if not full or full in repos:
                    continue
                repos.add(full)
                logger.info("[test-cycle] selected repo for %s: %s", proto, full)
                break
            if len(repos) >= self.test_cycle_repos:
                break
        return repos

    async def _collect_repo_content(
        self,
        client: httpx.AsyncClient,
        repo: str,
        out: list[tuple[str, str]],
        seen_sources: set[str],
    ) -> None:
        repo_meta = await self._get_json(client, f"https://api.github.com/repos/{repo}")
        if not repo_meta:
            return
        default_branch = repo_meta.get("default_branch", "main")

        readme = await self._get_json(client, f"https://api.github.com/repos/{repo}/readme")
        if readme:
            content = readme.get("content", "")
            if content:
                try:
                    decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                    source = f"https://github.com/{repo}#readme"
                    if source not in seen_sources:
                        seen_sources.add(source)
                        out.append((source, decoded))
                except Exception:
                    pass

        tree = await self._get_json(
            client,
            f"https://api.github.com/repos/{repo}/git/trees/{default_branch}",
            params={"recursive": 1},
        )
        if not tree:
            return

        entries = tree.get("tree", [])
        candidates = [
            e
            for e in entries
            if e.get("type") == "blob"
            and e.get("size", 0) <= self.max_blob_bytes
            and (TEXT_EXT_RE.search(e.get("path", "")) or PROXYISH_PATH_RE.search(e.get("path", "")))
        ]

        for blob in candidates[:200]:
            sha = blob.get("sha")
            path = blob.get("path", "")
            if not sha:
                continue
            payload = await self._get_json(client, f"https://api.github.com/repos/{repo}/git/blobs/{sha}")
            if not payload or payload.get("encoding") != "base64":
                continue
            try:
                decoded = base64.b64decode(payload.get("content", "")).decode("utf-8", errors="ignore")
            except Exception:
                continue
            source = f"https://github.com/{repo}/blob/{default_branch}/{path}"
            if source in seen_sources:
                continue
            seen_sources.add(source)
            out.append((source, decoded))
