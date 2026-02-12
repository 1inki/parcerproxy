from __future__ import annotations

from app.storage import Storage


class ProxyService:
    def __init__(self, db_url: str) -> None:
        self.storage = Storage(db_url)

    def get_alive(self, limit: int = 1000, countries: list[str] | None = None) -> list[dict]:
        rows = self.storage.top_alive(limit=limit, countries=countries)
        return [
            {
                "type": r.proxy_type,
                "host": r.host,
                "port": r.port,
                "country": r.country,
                "latency_ms": r.latency_ms,
                "score": r.score,
                "success_rate": r.success_rate,
                "source": r.source,
                "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
            }
            for r in rows
        ]
