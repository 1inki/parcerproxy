from __future__ import annotations

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
            # 1) Deep code-search pagination by keywords.
            for q in self.queries:
                for page in range(1, self.code_pages + 1):
                    search = await client.get(
                        "https://api.github.com/search/code",
                        params={"q": f"{q} in:file", "per_page": self.per_page, "page": page},
                    )
                    if search.status_code != 200:
                        break
                    items = search.json().get("items", [])
                    if not items:
                        break
                    for item in items:
                        url = item.get("url")
                        source = item.get("html_url", "github")
                        if not url or source in seen_sources:
                            continue
                        file_resp = await client.get(url)
                        if file_resp.status_code != 200:
                            continue
                        content = file_resp.json().get("content", "")
                        if not content:
                            continue
                        try:
                            decoded = base64.b64decode(content).decode("utf-8", errors="ignore")
                        except Exception:
                            continue
                        seen_sources.add(source)
                        out.append((source, decoded))

            # 2) Repo discovery by keywords + README parsing for breadth.
            discovered_repos: set[str] = set(self.extra_repos)
            for q in self.queries:
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
                        break
                    items = resp.json().get("items", [])
                    if not items:
                        break
                    discovered_repos.update(item.get("full_name", "").lower() for item in items if item.get("full_name"))

            # 3) Focused deep scan of discovered repos via git tree and selected blobs.
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
        repo_meta = await client.get(f"https://api.github.com/repos/{repo}")
        if repo_meta.status_code != 200:
            return
        default_branch = repo_meta.json().get("default_branch", "main")

        readme = await client.get(f"https://api.github.com/repos/{repo}/readme")
        if readme.status_code == 200:
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

        tree = await client.get(f"https://api.github.com/repos/{repo}/git/trees/{default_branch}", params={"recursive": 1})
        if tree.status_code != 200:
            return

        entries = tree.json().get("tree", [])
        candidates = [
            e for e in entries
            if e.get("type") == "blob"
            and e.get("size", 0) <= self.max_blob_bytes
            and (TEXT_EXT_RE.search(e.get("path", "")) or "proxy" in e.get("path", "").lower())
        ]

        for blob in candidates[:200]:
            sha = blob.get("sha")
            path = blob.get("path", "")
            if not sha:
                continue
            blob_resp = await client.get(f"https://api.github.com/repos/{repo}/git/blobs/{sha}")
            if blob_resp.status_code != 200:
                continue
            payload = blob_resp.json()
            if payload.get("encoding") != "base64":
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
