from __future__ import annotations

import asyncio
import base64
import re

import httpx

from app.collectors.base import Collector


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
    ) -> None:
        self.token = token
        self.queries = queries
        self.code_pages = code_pages
        self.repo_pages = repo_pages
        self.per_page = min(max(per_page, 1), 100)
        self.max_blob_bytes = max_blob_bytes
        self.extra_repos = extra_repos or []

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        params: dict | None = None,
    ) -> dict | None:
        try:
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError):
            return None

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
        timeout = httpx.Timeout(connect=10.0, read=20.0, write=20.0, pool=20.0)
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            for q in self.queries:
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

            discovered_repos: set[str] = set(self.extra_repos)
            for q in self.queries:
                for page in range(1, self.repo_pages + 1):
                    payload = await self._get_json(
                        client,
                        "https://api.github.com/search/repositories",
                        params={
                            "q": q,
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

            for repo in discovered_repos:
                await self._collect_repo_content(client, repo, out, seen_sources)

        return out

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
            and (TEXT_EXT_RE.search(e.get("path", "")) or "proxy" in e.get("path", "").lower())
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
